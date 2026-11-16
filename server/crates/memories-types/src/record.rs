//! The stored record. The known fields are typed. Each other field, and each
//! known field with a value that its type cannot hold without a change, stays
//! in `extra`. Thus a read and a write give back each field as it was.

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

/// One entry of `links`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Link {
    pub to_id: i64,
    #[serde(rename = "type")]
    pub link_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Record {
    pub id: u64,
    pub text: Option<String>,
    pub source: Option<String>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
    pub timestamp: Option<String>,
    pub last_reinforced_at: Option<String>,
    pub document_at: Option<String>,
    pub archived: Option<bool>,
    pub pinned: Option<bool>,
    pub is_latest: Option<bool>,
    pub entity_key: Option<String>,
    pub conflicts_with: Option<i64>,
    pub supersedes: Option<i64>,
    pub superseded_by: Option<i64>,
    pub links: Option<Vec<Link>>,
    /// The other fields, as stored.
    pub extra: Map<String, Value>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum RecordError {
    /// The value is not a JSON object.
    NotAnObject,
    /// `id` is absent or is not an integer from 0 to `i64::MAX`.
    BadId,
}

impl std::fmt::Display for RecordError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NotAnObject => f.write_str("record is not a JSON object"),
            Self::BadId => f.write_str("record id is not an integer from 0 to 2^63-1"),
        }
    }
}

impl std::error::Error for RecordError {}

/// Move `key` out of `map` into a typed value only if the typed value writes
/// back the same JSON. A null stays in the map, so null and absent stay apart.
fn take<T: DeserializeOwned + Serialize>(map: &mut Map<String, Value>, key: &str) -> Option<T> {
    let value = map.get(key).filter(|v| !v.is_null())?;
    let typed: T = serde_json::from_value(value.clone()).ok()?;
    if serde_json::to_value(&typed).ok().as_ref() != Some(value) {
        return None;
    }
    map.remove(key);
    Some(typed)
}

fn put<T: Serialize>(map: &mut Map<String, Value>, key: &str, value: &Option<T>) {
    if let Some(v) = value {
        let v = serde_json::to_value(v).expect("record fields serialize");
        map.insert(key.to_owned(), v);
    }
}

impl Record {
    /// A record from a Qdrant payload. The payload has no `id` field.
    pub fn from_payload(id: u64, mut map: Map<String, Value>) -> Self {
        Self {
            id,
            text: take(&mut map, "text"),
            source: take(&mut map, "source"),
            created_at: take(&mut map, "created_at"),
            updated_at: take(&mut map, "updated_at"),
            timestamp: take(&mut map, "timestamp"),
            last_reinforced_at: take(&mut map, "last_reinforced_at"),
            document_at: take(&mut map, "document_at"),
            archived: take(&mut map, "archived"),
            pinned: take(&mut map, "pinned"),
            is_latest: take(&mut map, "is_latest"),
            entity_key: take(&mut map, "entity_key"),
            conflicts_with: take(&mut map, "conflicts_with"),
            supersedes: take(&mut map, "supersedes"),
            superseded_by: take(&mut map, "superseded_by"),
            links: take(&mut map, "links"),
            extra: map,
        }
    }

    /// The Qdrant payload: all fields except `id`.
    pub fn to_payload(&self) -> Map<String, Value> {
        let mut map = self.extra.clone();
        put(&mut map, "text", &self.text);
        put(&mut map, "source", &self.source);
        put(&mut map, "created_at", &self.created_at);
        put(&mut map, "updated_at", &self.updated_at);
        put(&mut map, "timestamp", &self.timestamp);
        put(&mut map, "last_reinforced_at", &self.last_reinforced_at);
        put(&mut map, "document_at", &self.document_at);
        put(&mut map, "archived", &self.archived);
        put(&mut map, "pinned", &self.pinned);
        put(&mut map, "is_latest", &self.is_latest);
        put(&mut map, "entity_key", &self.entity_key);
        put(&mut map, "conflicts_with", &self.conflicts_with);
        put(&mut map, "supersedes", &self.supersedes);
        put(&mut map, "superseded_by", &self.superseded_by);
        put(&mut map, "links", &self.links);
        map
    }

    /// A record from an entry of `metadata.json` (with `id`).
    pub fn from_json(value: Value) -> Result<Self, RecordError> {
        let Value::Object(mut map) = value else {
            return Err(RecordError::NotAnObject);
        };
        let id = map
            .remove("id")
            .and_then(|v| v.as_u64())
            .filter(|&id| i64::try_from(id).is_ok())
            .ok_or(RecordError::BadId)?;
        Ok(Self::from_payload(id, map))
    }

    /// The record as JSON, with `id`.
    pub fn to_json(&self) -> Value {
        let mut map = Map::new();
        map.insert("id".to_owned(), self.id.into());
        map.extend(self.to_payload());
        Value::Object(map)
    }

    /// A field by its stored name, typed or not (`id` excluded).
    pub fn field(&self, key: &str) -> Option<Value> {
        self.to_payload().remove(key)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture(name: &str) -> Vec<Value> {
        let path = format!(
            "{}/../memories-store/tests/fixtures/{name}",
            env!("CARGO_MANIFEST_DIR")
        );
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    #[test]
    fn each_fixture_record_round_trips() {
        for name in ["seed_records.json", "edge_records.json"] {
            let records = fixture(name);
            assert!(!records.is_empty());
            for value in records {
                let record = Record::from_json(value.clone()).unwrap();
                assert_eq!(record.to_json(), value, "{name}");
            }
        }
    }

    #[test]
    fn known_fields_are_typed() {
        let r = Record::from_json(json!({
            "id": 5, "text": "t", "source": "s", "pinned": true, "conflicts_with": 3,
            "links": [{"to_id": 2, "type": "related_to", "created_at": "x"}], "other": 1
        }))
        .unwrap();
        assert_eq!(r.text.as_deref(), Some("t"));
        assert_eq!(r.pinned, Some(true));
        assert_eq!(r.conflicts_with, Some(3));
        assert_eq!(r.links.as_ref().unwrap()[0].link_type, "related_to");
        assert_eq!(r.extra, json!({"other": 1}).as_object().unwrap().clone());
    }

    #[test]
    fn values_the_type_cannot_hold_stay_in_extra() {
        let value = json!({
            "id": 1, "pinned": "yes", "conflicts_with": "17", "archived": null,
            "created_at": 5, "supersedes": 2.0,
            "links": [{"to_id": 1, "type": "x", "note": "n"}]
        });
        let r = Record::from_json(value.clone()).unwrap();
        assert_eq!(r.pinned, None);
        assert_eq!(r.conflicts_with, None);
        assert_eq!(r.archived, None);
        assert_eq!(r.created_at, None);
        assert_eq!(r.supersedes, None);
        assert_eq!(r.links, None);
        assert_eq!(r.extra.len(), 6);
        assert_eq!(r.to_json(), value);
    }

    #[test]
    fn bad_ids_are_refused() {
        for v in [
            json!({}),
            json!({"id": -1}),
            json!({"id": "1"}),
            json!({"id": 1.5}),
        ] {
            assert_eq!(Record::from_json(v), Err(RecordError::BadId));
        }
        assert_eq!(
            Record::from_json(json!({"id": 9_223_372_036_854_775_808u64})),
            Err(RecordError::BadId)
        );
        assert_eq!(Record::from_json(json!([1])), Err(RecordError::NotAnObject));
    }
}
