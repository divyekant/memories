//! The Qdrant store of the Rust server (collection `memories_v2`). Each point
//! holds the dense vector, the keyword vector and the full record as payload.

use std::collections::{BTreeMap, HashMap};
use std::sync::Mutex;

use memories_types::Record;
use qdrant_client::qdrant::{
    Condition, CountPointsBuilder, CreateCollectionBuilder, CreateFieldIndexCollectionBuilder,
    DeletePointsBuilder, Distance, FacetCountsBuilder, FieldType, Filter, GetPointsBuilder,
    Modifier, PointId, PointStruct, PointsIdsList, QueryPointsBuilder, ReadConsistency,
    RetrievedPoint, ScoredPoint, ScrollPointsBuilder, SetPayloadPointsBuilder,
    SparseVectorParamsBuilder, SparseVectorsConfigBuilder, UpsertPointsBuilder, Value, Vector,
    VectorParamsBuilder, VectorsConfigBuilder, WriteOrdering, facet_value, point_id,
    vectors_config,
};
use qdrant_client::{Payload, Qdrant};
use serde_json::{Map, Value as Json};

use crate::settings::QdrantSettings;

/// The name of the dense vector.
pub const DENSE_VECTOR: &str = "dense";
/// The name of the keyword (BM25 sparse) vector of decision P0-2.
pub const KEYWORD_VECTOR: &str = "keyword";

/// The payload indexes. `links[].to_id` lets a delete find the links to a record.
const PAYLOAD_INDEXES: [(&str, FieldType); 8] = [
    ("source", FieldType::Keyword),
    ("archived", FieldType::Bool),
    ("pinned", FieldType::Bool),
    ("document_at", FieldType::Keyword),
    ("is_latest", FieldType::Bool),
    ("entity_key", FieldType::Keyword),
    ("conflicts_with", FieldType::Integer),
    ("links[].to_id", FieldType::Integer),
];

const ID_PAGE: u32 = 10_000;

#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    #[error("qdrant: {0}")]
    Qdrant(#[from] qdrant_client::QdrantError),
    /// Qdrant keeps an integer as i64. A larger integer would come back as a
    /// float, so the store refuses it.
    #[error("record {id}: field {field:?} has an integer above 2^63-1")]
    IntegerTooLarge { id: u64, field: String },
    #[error("record {id}: the dense vector has {got} values, not {want}")]
    BadDimension { id: u64, got: usize, want: u64 },
    #[error("qdrant returned a point with a UUID id")]
    UuidPoint,
    /// The point ID is the record ID. A payload `id` would hide it.
    #[error("record {id}: the payload must not have an \"id\" field")]
    IdInPayload { id: u64 },
    #[error("collection {collection} exists with another vector config: {detail}")]
    BadCollection { collection: String, detail: String },
    /// A call of the Qdrant REST API (snapshot files) failed.
    #[error("qdrant rest: {0}")]
    Rest(String),
    #[error("snapshot file: {0}")]
    Io(#[from] std::io::Error),
}

impl From<reqwest::Error> for StoreError {
    fn from(e: reqwest::Error) -> Self {
        Self::Rest(e.to_string())
    }
}

/// A point to write.
#[derive(Clone, Debug)]
pub struct NewPoint {
    pub record: Record,
    pub dense: Vec<f32>,
    /// (token index, weight) pairs of the keyword vector.
    pub keyword: Vec<(u32, f32)>,
}

/// The vectors of one stored point. `dense` comes from the vector `dense`, or
/// from the unnamed vector of a Python (v5) collection.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct StoredVectors {
    pub dense: Option<Vec<f32>>,
    pub keyword: Option<Vec<(u32, f32)>>,
}

pub struct QdrantStore {
    client: Qdrant,
    collection: String,
    wait: bool,
    ordering: WriteOrdering,
    consistency: ReadConsistency,
    replication_factor: u32,
    write_consistency_factor: u32,
    /// Qdrant data calls by operation name, for the write cost tests.
    ops: Mutex<BTreeMap<&'static str, u64>>,
    /// The REST URL and key, for the snapshot files (gRPC does not send them).
    rest_url: String,
    api_key: Option<String>,
    http: reqwest::Client,
}

/// Percent-encode a path segment (each byte that is not unreserved).
fn urlencode(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                (b as char).to_string()
            }
            _ => format!("%{b:02X}"),
        })
        .collect()
}

fn too_large(value: &Json) -> bool {
    match value {
        Json::Number(n) => n.is_u64() && n.as_i64().is_none(),
        Json::Array(a) => a.iter().any(too_large),
        Json::Object(o) => o.values().any(too_large),
        _ => false,
    }
}

fn check_fields(id: u64, fields: &Map<String, Json>) -> Result<(), StoreError> {
    if fields.contains_key("id") {
        return Err(StoreError::IdInPayload { id });
    }
    if let Some((field, _)) = fields.iter().find(|(_, v)| too_large(v)) {
        return Err(StoreError::IntegerTooLarge {
            id,
            field: field.clone(),
        });
    }
    Ok(())
}

fn checked_payload(record: &Record) -> Result<Map<String, Json>, StoreError> {
    let payload = record.to_payload();
    check_fields(record.id, &payload)?;
    Ok(payload)
}

fn numeric_id(id: Option<PointId>) -> Result<u64, StoreError> {
    match id.and_then(|p| p.point_id_options) {
        Some(point_id::PointIdOptions::Num(n)) => Ok(n),
        _ => Err(StoreError::UuidPoint),
    }
}

fn to_record(id: Option<PointId>, payload: HashMap<String, Value>) -> Result<Record, StoreError> {
    let map = payload
        .into_iter()
        .map(|(k, v)| (k, v.into_json()))
        .collect();
    Ok(Record::from_payload(numeric_id(id)?, map))
}

fn retrieved(point: RetrievedPoint) -> Result<Record, StoreError> {
    to_record(point.id, point.payload)
}

fn scored(point: ScoredPoint) -> Result<(Record, f32), StoreError> {
    Ok((to_record(point.id, point.payload)?, point.score))
}

/// The scope filter of `_build_source_filter`: `source` in `sources` (when
/// given; an empty list matches no record), and no archived record unless
/// `include_archived`.
pub fn scope_filter(sources: Option<&[String]>, include_archived: bool) -> Option<Filter> {
    let must: Vec<Condition> = match sources {
        None => Vec::new(),
        Some([]) => vec![Condition::matches("source", "__no_match__".to_string())],
        Some(list) => vec![Condition::matches("source", list.to_vec())],
    };
    let must_not = if include_archived {
        Vec::new()
    } else {
        vec![Condition::matches("archived", true)]
    };
    if must.is_empty() && must_not.is_empty() {
        return None;
    }
    Some(Filter {
        must,
        must_not,
        ..Default::default()
    })
}

fn ids(ids: &[u64]) -> Vec<PointId> {
    ids.iter().map(|&id| id.into()).collect()
}

impl QdrantStore {
    /// A client for `collection` on the server of `settings`. The call does
    /// not connect; the first request does.
    pub fn new(settings: &QdrantSettings, collection: &str) -> Result<Self, StoreError> {
        let client = Qdrant::from_url(&settings.grpc_url)
            .api_key(settings.api_key.clone())
            .skip_compatibility_check()
            .build()?;
        Ok(Self {
            client,
            collection: collection.to_owned(),
            wait: settings.wait,
            ordering: WriteOrdering {
                r#type: settings.write_ordering as i32,
            },
            consistency: ReadConsistency {
                value: Some(settings.read_consistency),
            },
            replication_factor: settings.replication_factor,
            write_consistency_factor: settings.write_consistency_factor,
            ops: Mutex::default(),
            rest_url: settings.url.trim_end_matches('/').to_owned(),
            api_key: settings.api_key.clone(),
            // As the gRPC client: no HTTP proxy from the environment.
            http: reqwest::Client::builder()
                .no_proxy()
                .build()
                .map_err(StoreError::from)?,
        })
    }

    fn op(&self, name: &'static str) {
        *self
            .ops
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .entry(name)
            .or_default() += 1;
    }

    /// The count of Qdrant data calls of each operation since the start.
    pub fn op_counts(&self) -> BTreeMap<&'static str, u64> {
        self.ops.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn collection(&self) -> &str {
        &self.collection
    }

    pub async fn exists(&self) -> Result<bool, StoreError> {
        Ok(self.client.collection_exists(&self.collection).await?)
    }

    /// The vectors of the points of `wanted` that exist.
    pub async fn vectors(&self, wanted: &[u64]) -> Result<HashMap<u64, StoredVectors>, StoreError> {
        use qdrant_client::qdrant::vector_output::Vector as Out;
        if wanted.is_empty() {
            return Ok(HashMap::new());
        }
        let response = self
            .client
            .get_points(
                GetPointsBuilder::new(&self.collection, ids(wanted))
                    .with_payload(false)
                    .with_vectors(true)
                    .read_consistency(self.consistency.value.expect("set in new")),
            )
            .await?;
        let mut out = HashMap::new();
        for p in response.result {
            let id = numeric_id(p.id)?;
            let mut v = StoredVectors::default();
            if let Some(vs) = p.vectors {
                let dense = vs
                    .get_vector_by_name(DENSE_VECTOR)
                    .or_else(|| vs.get_vector());
                if let Some(Out::Dense(d)) = dense {
                    v.dense = Some(d.data);
                }
                if let Some(Out::Sparse(sp)) = vs.get_vector_by_name(KEYWORD_VECTOR) {
                    v.keyword = Some(sp.indices.into_iter().zip(sp.values).collect());
                }
            }
            out.insert(id, v);
        }
        Ok(out)
    }

    /// All point IDs, in ID order.
    pub async fn ids(&self) -> Result<Vec<u64>, StoreError> {
        let mut out = Vec::new();
        let mut offset: Option<PointId> = None;
        loop {
            let mut request = ScrollPointsBuilder::new(&self.collection)
                .limit(ID_PAGE)
                .with_payload(false)
                .with_vectors(false)
                .read_consistency(self.consistency.value.expect("set in new"));
            if let Some(o) = offset.take() {
                request = request.offset(o);
            }
            let response = self.client.scroll(request).await?;
            for p in response.result {
                out.push(numeric_id(p.id)?);
            }
            match response.next_page_offset {
                Some(o) => offset = Some(o),
                None => return Ok(out),
            }
        }
    }

    /// Make the collection and its payload indexes if they do not exist.
    /// Returns true if the call made the collection. An existing collection
    /// must have the same vectors, or the call fails.
    pub async fn ensure_collection(&self, dim: u64) -> Result<bool, StoreError> {
        let mut created = false;
        if !self.client.collection_exists(&self.collection).await? {
            let mut sparse = SparseVectorsConfigBuilder::default();
            sparse.add_named_vector_params(
                KEYWORD_VECTOR,
                SparseVectorParamsBuilder::default().modifier(Modifier::Idf as i32),
            );
            let mut dense = VectorsConfigBuilder::default();
            dense.add_named_vector_params(
                DENSE_VECTOR,
                VectorParamsBuilder::new(dim, Distance::Cosine),
            );
            let made = self
                .client
                .create_collection(
                    CreateCollectionBuilder::new(&self.collection)
                        .vectors_config(dense)
                        .sparse_vectors_config(sparse)
                        .replication_factor(self.replication_factor)
                        .write_consistency_factor(self.write_consistency_factor),
                )
                .await;
            match made {
                Ok(_) => created = true,
                // A second process made it between the check and the create.
                Err(e) if !self.client.collection_exists(&self.collection).await? => {
                    return Err(e.into());
                }
                Err(_) => {}
            }
        }
        if !created {
            self.check_vectors(dim).await?;
        }
        for (field, kind) in PAYLOAD_INDEXES {
            self.client
                .create_field_index(
                    CreateFieldIndexCollectionBuilder::new(&self.collection, field, kind)
                        .wait(true),
                )
                .await?;
        }
        Ok(created)
    }

    async fn check_vectors(&self, dim: u64) -> Result<(), StoreError> {
        let bad = |detail: String| StoreError::BadCollection {
            collection: self.collection.clone(),
            detail,
        };
        let info = self.client.collection_info(&self.collection).await?;
        let params = info
            .result
            .and_then(|r| r.config)
            .and_then(|c| c.params)
            .ok_or_else(|| bad("no config".into()))?;
        let dense = params
            .vectors_config
            .and_then(|v| v.config)
            .and_then(|c| match c {
                vectors_config::Config::ParamsMap(m) => m.map.get(DENSE_VECTOR).cloned(),
                vectors_config::Config::Params(_) => None,
            })
            .ok_or_else(|| bad(format!("no dense vector {DENSE_VECTOR:?}")))?;
        if dense.size != dim || dense.distance != Distance::Cosine as i32 {
            return Err(bad(format!(
                "dense vector size {} distance {}, want {dim} cosine",
                dense.size, dense.distance
            )));
        }
        let sparse = params
            .sparse_vectors_config
            .and_then(|s| s.map.get(KEYWORD_VECTOR).cloned())
            .ok_or_else(|| bad(format!("no sparse vector {KEYWORD_VECTOR:?}")))?;
        if sparse.modifier != Some(Modifier::Idf as i32) {
            return Err(bad("the keyword vector has no idf modifier".into()));
        }
        Ok(())
    }

    /// Make a Qdrant snapshot of the collection. Returns its name.
    pub async fn create_snapshot(&self) -> Result<String, StoreError> {
        self.op("create_snapshot");
        let r = self.client.create_snapshot(&self.collection).await?;
        Ok(r.snapshot_description.map(|d| d.name).unwrap_or_default())
    }

    fn rest(&self, method: reqwest::Method, path: &str) -> reqwest::RequestBuilder {
        let url = format!(
            "{}/collections/{}/snapshots{path}",
            self.rest_url, self.collection
        );
        let r = self.http.request(method, url);
        match &self.api_key {
            Some(k) => r.header("api-key", k),
            None => r,
        }
    }

    async fn checked(r: reqwest::RequestBuilder) -> Result<reqwest::Response, StoreError> {
        let resp = r.send().await?;
        let status = resp.status();
        if status.is_success() {
            return Ok(resp);
        }
        let body = resp.text().await.unwrap_or_default();
        Err(StoreError::Rest(format!(
            "HTTP {status}: {}",
            body.chars().take(300).collect::<String>()
        )))
    }

    /// Write the snapshot `name` of the collection to `dest`.
    pub async fn download_snapshot(
        &self,
        name: &str,
        dest: &std::path::Path,
    ) -> Result<(), StoreError> {
        use tokio::io::AsyncWriteExt;
        let path = format!("/{}", urlencode(name));
        let mut resp = Self::checked(self.rest(reqwest::Method::GET, &path)).await?;
        let mut file = tokio::fs::File::create(dest).await?;
        while let Some(chunk) = resp.chunk().await? {
            file.write_all(&chunk).await?;
        }
        file.sync_all().await?;
        Ok(())
    }

    pub async fn delete_snapshot(&self, name: &str) -> Result<(), StoreError> {
        let path = format!("/{}?wait=true", urlencode(name));
        Self::checked(self.rest(reqwest::Method::DELETE, &path)).await?;
        Ok(())
    }

    /// Replace the collection with the points of a snapshot file
    /// (`priority=snapshot`). The file streams; it does not go into memory.
    pub async fn upload_snapshot(&self, file: &std::path::Path) -> Result<(), StoreError> {
        use futures_util::StreamExt;
        use futures_util::stream;
        use tokio::io::AsyncReadExt;
        let boundary = "memories-snapshot-boundary-7f3a9c";
        let head = format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"snapshot\"; filename=\"collection.snapshot\"\r\nContent-Type: application/octet-stream\r\n\r\n"
        );
        let tail = format!("\r\n--{boundary}--\r\n");
        let f = tokio::fs::File::open(file).await?;
        let len = f.metadata().await?.len() + head.len() as u64 + tail.len() as u64;
        let chunks = stream::unfold(f, |mut f| async move {
            let mut buf = vec![0u8; 1 << 20];
            match f.read(&mut buf).await {
                Ok(0) => None,
                Ok(n) => {
                    buf.truncate(n);
                    Some((Ok(buf), f))
                }
                Err(e) => Some((Err(e), f)),
            }
        });
        let body = stream::once(async move { Ok(head.into_bytes()) })
            .chain(chunks)
            .chain(stream::once(async move { Ok(tail.into_bytes()) }));
        let r = self
            .rest(reqwest::Method::POST, "/upload?priority=snapshot&wait=true")
            .header(
                reqwest::header::CONTENT_TYPE,
                format!("multipart/form-data; boundary={boundary}"),
            )
            .header(reqwest::header::CONTENT_LENGTH, len)
            .body(reqwest::Body::wrap_stream(body));
        Self::checked(r).await?;
        Ok(())
    }

    pub async fn delete_collection(&self) -> Result<(), StoreError> {
        self.client.delete_collection(&self.collection).await?;
        Ok(())
    }

    /// Write whole points. A point with the same ID is replaced.
    pub async fn upsert(&self, points: Vec<NewPoint>, dim: u64) -> Result<(), StoreError> {
        self.op("upsert");
        if points.is_empty() {
            return Ok(());
        }
        let mut structs = Vec::with_capacity(points.len());
        for p in points {
            if p.dense.len() as u64 != dim {
                return Err(StoreError::BadDimension {
                    id: p.record.id,
                    got: p.dense.len(),
                    want: dim,
                });
            }
            let payload = Payload::from(checked_payload(&p.record)?);
            let (indices, values): (Vec<u32>, Vec<f32>) = p.keyword.into_iter().unzip();
            let vectors: HashMap<String, Vector> = HashMap::from([
                (DENSE_VECTOR.to_owned(), Vector::new_dense(p.dense)),
                (
                    KEYWORD_VECTOR.to_owned(),
                    Vector::new_sparse(indices, values),
                ),
            ]);
            structs.push(PointStruct::new(p.record.id, vectors, payload));
        }
        self.client
            .upsert_points(
                UpsertPointsBuilder::new(&self.collection, structs)
                    .wait(self.wait)
                    .ordering(self.ordering),
            )
            .await?;
        Ok(())
    }

    pub async fn get(&self, id: u64) -> Result<Option<Record>, StoreError> {
        Ok(self.get_many(&[id]).await?.into_iter().next())
    }

    /// The records of `ids` that exist, in the order of `ids`.
    pub async fn get_many(&self, wanted: &[u64]) -> Result<Vec<Record>, StoreError> {
        self.op("get_many");
        if wanted.is_empty() {
            return Ok(Vec::new());
        }
        let response = self
            .client
            .get_points(
                GetPointsBuilder::new(&self.collection, ids(wanted))
                    .with_payload(true)
                    .with_vectors(false)
                    .read_consistency(self.consistency.value.expect("set in new")),
            )
            .await?;
        let mut found: HashMap<u64, Record> = HashMap::new();
        for point in response.result {
            let r = retrieved(point)?;
            found.insert(r.id, r);
        }
        // A repeated ID gives the record again, as `get_memories` does.
        Ok(wanted
            .iter()
            .filter_map(|id| found.get(id).cloned())
            .collect())
    }

    /// Merge `fields` into the payload of each point of `ids`. The vectors do
    /// not change.
    pub async fn set_payload(
        &self,
        id_list: &[u64],
        fields: Map<String, Json>,
    ) -> Result<(), StoreError> {
        self.op("set_payload");
        check_fields(id_list.first().copied().unwrap_or_default(), &fields)?;
        self.client
            .set_payload(
                SetPayloadPointsBuilder::new(&self.collection, Payload::from(fields))
                    .points_selector(PointsIdsList { ids: ids(id_list) })
                    .wait(self.wait)
                    .ordering(self.ordering),
            )
            .await?;
        Ok(())
    }

    /// Like [`Self::set_payload`], but an ID with no point is skipped, not an
    /// error (the selector is a `has_id` filter).
    pub async fn set_payload_existing(
        &self,
        id_list: &[u64],
        fields: Map<String, Json>,
    ) -> Result<(), StoreError> {
        self.op("set_payload_existing");
        check_fields(id_list.first().copied().unwrap_or_default(), &fields)?;
        let filter = Filter::must([Condition::has_id(id_list.iter().copied())]);
        self.client
            .set_payload(
                SetPayloadPointsBuilder::new(&self.collection, Payload::from(fields))
                    .points_selector(filter)
                    .wait(self.wait)
                    .ordering(self.ordering),
            )
            .await?;
        Ok(())
    }

    /// Replace the full payload of the point of `record` (a field that the
    /// record does not have is removed). The vectors do not change.
    pub async fn replace_payload(&self, record: &Record) -> Result<(), StoreError> {
        self.op("replace_payload");
        let payload = Payload::from(checked_payload(record)?);
        self.client
            .overwrite_payload(
                SetPayloadPointsBuilder::new(&self.collection, payload)
                    .points_selector(PointsIdsList {
                        ids: ids(&[record.id]),
                    })
                    .wait(self.wait)
                    .ordering(self.ordering),
            )
            .await?;
        Ok(())
    }

    pub async fn delete(&self, id_list: &[u64]) -> Result<(), StoreError> {
        self.op("delete");
        if id_list.is_empty() {
            return Ok(());
        }
        self.client
            .delete_points(
                DeletePointsBuilder::new(&self.collection)
                    .points(PointsIdsList { ids: ids(id_list) })
                    .wait(self.wait)
                    .ordering(self.ordering),
            )
            .await?;
        Ok(())
    }

    /// One page of records in ID order, from `offset` (an ID). Returns the
    /// records and the offset of the next page.
    pub async fn scroll(
        &self,
        filter: Option<Filter>,
        offset: Option<u64>,
        limit: u32,
    ) -> Result<(Vec<Record>, Option<u64>), StoreError> {
        self.op("scroll");
        let mut request = ScrollPointsBuilder::new(&self.collection)
            .limit(limit)
            .with_payload(true)
            .with_vectors(false)
            .read_consistency(self.consistency.value.expect("set in new"));
        if let Some(f) = filter {
            request = request.filter(f);
        }
        if let Some(o) = offset {
            request = request.offset(o);
        }
        let response = self.client.scroll(request).await?;
        let records = response
            .result
            .into_iter()
            .map(retrieved)
            .collect::<Result<_, _>>()?;
        let next = match response.next_page_offset {
            Some(p) => Some(numeric_id(Some(p))?),
            None => None,
        };
        Ok((records, next))
    }

    /// The nearest points to `vector` by cosine similarity, high first, with
    /// the score. Only scores of `threshold` or more.
    pub async fn search_dense(
        &self,
        vector: Vec<f32>,
        limit: u64,
        threshold: Option<f32>,
        filter: Option<Filter>,
    ) -> Result<Vec<(Record, f32)>, StoreError> {
        self.op("search_dense");
        let mut request = QueryPointsBuilder::new(&self.collection)
            .query(vector)
            .using(DENSE_VECTOR)
            .limit(limit)
            .with_payload(true)
            .read_consistency(self.consistency.value.expect("set in new"));
        if let Some(t) = threshold {
            request = request.score_threshold(t);
        }
        if let Some(f) = filter {
            request = request.filter(f);
        }
        let response = self.client.query(request).await?;
        response.result.into_iter().map(scored).collect()
    }

    /// The keyword hits of the sparse `query` (token index, weight), high
    /// first. Qdrant adds the IDF. Hits with score 0 are not returned.
    pub async fn search_keyword(
        &self,
        query: Vec<(u32, f32)>,
        limit: u64,
        filter: Option<Filter>,
    ) -> Result<Vec<(Record, f32)>, StoreError> {
        self.op("search_keyword");
        if query.is_empty() {
            return Ok(Vec::new());
        }
        let (indices, values): (Vec<u32>, Vec<f32>) = query.into_iter().unzip();
        let mut request = QueryPointsBuilder::new(&self.collection)
            .query(qdrant_client::qdrant::Query::new_nearest(
                qdrant_client::qdrant::VectorInput::new_sparse(indices, values),
            ))
            .using(KEYWORD_VECTOR)
            .limit(limit)
            .with_payload(true)
            .read_consistency(self.consistency.value.expect("set in new"));
        if let Some(f) = filter {
            request = request.filter(f);
        }
        let response = self.client.query(request).await?;
        response
            .result
            .into_iter()
            .filter(|p| p.score > 0.0)
            .map(scored)
            .collect()
    }

    /// The exact count of the points that match `filter`.
    pub async fn count(&self, filter: Option<Filter>) -> Result<u64, StoreError> {
        self.op("count");
        let mut request = CountPointsBuilder::new(&self.collection)
            .exact(true)
            .read_consistency(self.consistency.value.expect("set in new"));
        if let Some(f) = filter {
            request = request.filter(f);
        }
        Ok(self
            .client
            .count(request)
            .await?
            .result
            .map_or(0, |r| r.count))
    }

    /// Exact counts of the values of the indexed keyword field `key`, at most
    /// `limit` values.
    pub async fn facet(
        &self,
        key: &str,
        filter: Option<Filter>,
        limit: u64,
    ) -> Result<Vec<(String, u64)>, StoreError> {
        self.op("facet");
        let mut request = FacetCountsBuilder::new(&self.collection, key)
            .limit(limit)
            .exact(true)
            .read_consistency(self.consistency);
        if let Some(f) = filter {
            request = request.filter(f);
        }
        let response = self.client.facet(request).await?;
        Ok(response
            .hits
            .into_iter()
            .filter_map(|hit| {
                let value = match hit.value?.variant? {
                    facet_value::Variant::StringValue(s) => s,
                    facet_value::Variant::IntegerValue(i) => i.to_string(),
                    facet_value::Variant::BoolValue(b) => b.to_string(),
                };
                Some((value, hit.count))
            })
            .collect())
    }

    /// The highest point ID, or None for an empty collection. The ID counter
    /// starts above it.
    pub async fn max_id(&self) -> Result<Option<u64>, StoreError> {
        // shortcut: reads every ID (about 1 s per 1M points); use an order_by
        // on an indexed id payload field if start time becomes a problem.
        Ok(self.ids().await?.last().copied())
    }
}
