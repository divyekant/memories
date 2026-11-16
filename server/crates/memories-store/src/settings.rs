//! The `QDRANT_*` settings, with the defaults of `qdrant_config.py` (v5.19.0).

use qdrant_client::qdrant::{ReadConsistencyType, WriteOrderingType, read_consistency};

const REST_PORT: u16 = 6333;
const GRPC_PORT: u16 = 6334;

#[derive(Clone, Debug, PartialEq)]
pub struct QdrantSettings {
    /// `QDRANT_URL` as given (the REST address).
    pub url: String,
    /// The gRPC address that the Rust client uses.
    pub grpc_url: String,
    pub api_key: Option<String>,
    /// `QDRANT_COLLECTION`: the collection of the Python server.
    pub collection: String,
    pub wait: bool,
    pub write_ordering: WriteOrderingType,
    pub read_consistency: read_consistency::Value,
    pub replication_factor: u32,
    pub write_consistency_factor: u32,
}

#[derive(Debug, thiserror::Error, PartialEq)]
pub enum SettingsError {
    #[error(
        "QDRANT_URL is empty. The Rust server needs a Qdrant server; the embedded mode of the Python server is not available"
    )]
    NoUrl,
    #[error("QDRANT_URL {0:?} is not an http or https URL")]
    BadUrl(String),
    #[error("{name} has the value {value:?}, which is not valid")]
    BadValue { name: &'static str, value: String },
}

fn env_bool(raw: Option<&str>, default: bool) -> bool {
    match raw {
        None => default,
        Some(v) => matches!(
            v.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "y" | "on"
        ),
    }
}

/// `_env_int` of Python: an empty or bad value gives the default, and the
/// minimum is 1.
fn env_int(raw: Option<&str>, default: u32) -> u32 {
    raw.map(str::trim)
        .filter(|v| !v.is_empty())
        .and_then(|v| v.parse::<i64>().ok())
        .map_or(default, |v| v.clamp(1, i64::from(u32::MAX)) as u32)
        .max(1)
}

fn text(raw: Option<&str>, default: &str) -> String {
    raw.map(str::trim)
        .filter(|v| !v.is_empty())
        .unwrap_or(default)
        .to_owned()
}

/// The gRPC URL for a REST `QDRANT_URL`. Port 6333, or no port, becomes 6334.
/// `QDRANT_GRPC_PORT` sets the port in all cases. Another port stays as it is.
pub fn grpc_url(url: &str, grpc_port: Option<u16>) -> Result<String, SettingsError> {
    let bad = || SettingsError::BadUrl(url.to_owned());
    let (scheme, rest) = url.split_once("://").ok_or_else(bad)?;
    if scheme != "http" && scheme != "https" {
        return Err(bad());
    }
    let (authority, path) = rest.split_at(rest.find('/').unwrap_or(rest.len()));
    let (userinfo, hostport) = match authority.rsplit_once('@') {
        Some((u, h)) => (format!("{u}@"), h),
        None => (String::new(), authority),
    };
    // A port follows the last ':' that is after the closing ']' of an IPv6 host.
    let host_end = hostport.rfind(']').map_or(0, |i| i + 1);
    let (host, port) = match hostport[host_end..].rfind(':') {
        Some(i) => {
            let i = host_end + i;
            let port: u16 = hostport[i + 1..].parse().map_err(|_| bad())?;
            (&hostport[..i], Some(port))
        }
        None => (hostport, None),
    };
    if host.is_empty() {
        return Err(bad());
    }
    let port = match (grpc_port, port) {
        (Some(p), _) => p,
        (None, None | Some(REST_PORT)) => GRPC_PORT,
        (None, Some(p)) => p,
    };
    let path = path.trim_end_matches('/');
    Ok(format!("{scheme}://{userinfo}{host}:{port}{path}"))
}

impl QdrantSettings {
    pub fn from_env() -> Result<Self, SettingsError> {
        Self::from_lookup(|name| std::env::var(name).ok())
    }

    pub fn from_lookup(get: impl Fn(&str) -> Option<String>) -> Result<Self, SettingsError> {
        let raw = |name: &str| get(name);
        let url = text(raw("QDRANT_URL").as_deref(), "");
        if url.is_empty() {
            return Err(SettingsError::NoUrl);
        }
        let grpc_port = match raw("QDRANT_GRPC_PORT").as_deref().map(str::trim) {
            None | Some("") => None,
            Some(v) => Some(v.parse::<u16>().ok().filter(|&p| p > 0).ok_or(
                SettingsError::BadValue {
                    name: "QDRANT_GRPC_PORT",
                    value: v.to_owned(),
                },
            )?),
        };
        let ordering = text(raw("QDRANT_WRITE_ORDERING").as_deref(), "strong");
        let write_ordering = match ordering.as_str() {
            "weak" => WriteOrderingType::Weak,
            "medium" => WriteOrderingType::Medium,
            "strong" => WriteOrderingType::Strong,
            _ => {
                return Err(SettingsError::BadValue {
                    name: "QDRANT_WRITE_ORDERING",
                    value: ordering,
                });
            }
        };
        let consistency = text(raw("QDRANT_READ_CONSISTENCY").as_deref(), "majority");
        let read_consistency = match consistency.as_str() {
            "all" => read_consistency::Value::Type(ReadConsistencyType::All as i32),
            "majority" => read_consistency::Value::Type(ReadConsistencyType::Majority as i32),
            "quorum" => read_consistency::Value::Type(ReadConsistencyType::Quorum as i32),
            v => read_consistency::Value::Factor(v.parse().ok().filter(|&n| n > 0).ok_or(
                SettingsError::BadValue {
                    name: "QDRANT_READ_CONSISTENCY",
                    value: consistency.clone(),
                },
            )?),
        };
        Ok(Self {
            grpc_url: grpc_url(&url, grpc_port)?,
            url,
            api_key: Some(text(raw("QDRANT_API_KEY").as_deref(), "")).filter(|k| !k.is_empty()),
            collection: text(raw("QDRANT_COLLECTION").as_deref(), "memories"),
            wait: env_bool(raw("QDRANT_WAIT").as_deref(), true),
            write_ordering,
            read_consistency,
            replication_factor: env_int(raw("QDRANT_REPLICATION_FACTOR").as_deref(), 1),
            write_consistency_factor: env_int(raw("QDRANT_WRITE_CONSISTENCY_FACTOR").as_deref(), 1),
        })
    }

    /// The collection of the Rust server: `<QDRANT_COLLECTION>_v2`.
    pub fn v2_collection(&self) -> String {
        format!("{}_v2", self.collection)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn settings(pairs: &[(&str, &str)]) -> Result<QdrantSettings, SettingsError> {
        let map: HashMap<String, String> = pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
            .collect();
        QdrantSettings::from_lookup(|k| map.get(k).cloned())
    }

    #[test]
    fn defaults_match_python() {
        let s = settings(&[("QDRANT_URL", "http://qdrant:6333")]).unwrap();
        assert_eq!(s.grpc_url, "http://qdrant:6334");
        assert_eq!(s.api_key, None);
        assert_eq!(s.collection, "memories");
        assert_eq!(s.v2_collection(), "memories_v2");
        assert!(s.wait);
        assert_eq!(s.write_ordering, WriteOrderingType::Strong);
        assert_eq!(
            s.read_consistency,
            read_consistency::Value::Type(ReadConsistencyType::Majority as i32)
        );
        assert_eq!((s.replication_factor, s.write_consistency_factor), (1, 1));
    }

    #[test]
    fn values_from_env() {
        let s = settings(&[
            ("QDRANT_URL", " https://q.example "),
            ("QDRANT_API_KEY", "k"),
            ("QDRANT_COLLECTION", "mine"),
            ("QDRANT_WAIT", "off"),
            ("QDRANT_WRITE_ORDERING", "weak"),
            ("QDRANT_READ_CONSISTENCY", "2"),
            ("QDRANT_REPLICATION_FACTOR", "0"),
            ("QDRANT_WRITE_CONSISTENCY_FACTOR", "x"),
        ])
        .unwrap();
        assert_eq!(s.grpc_url, "https://q.example:6334");
        assert_eq!(s.api_key.as_deref(), Some("k"));
        assert_eq!(s.v2_collection(), "mine_v2");
        assert!(!s.wait);
        assert_eq!(s.write_ordering, WriteOrderingType::Weak);
        assert_eq!(s.read_consistency, read_consistency::Value::Factor(2));
        assert_eq!((s.replication_factor, s.write_consistency_factor), (1, 1));
    }

    #[test]
    fn bad_values_are_refused() {
        assert_eq!(settings(&[]), Err(SettingsError::NoUrl));
        assert_eq!(settings(&[("QDRANT_URL", "  ")]), Err(SettingsError::NoUrl));
        assert!(settings(&[("QDRANT_URL", "qdrant:6333")]).is_err());
        for (name, value) in [
            ("QDRANT_WRITE_ORDERING", "Strong"),
            ("QDRANT_WRITE_ORDERING", "fast"),
            ("QDRANT_READ_CONSISTENCY", "0"),
            ("QDRANT_READ_CONSISTENCY", "some"),
            ("QDRANT_GRPC_PORT", "x"),
        ] {
            assert!(
                settings(&[("QDRANT_URL", "http://q:6333"), (name, value)]).is_err(),
                "{name}={value}"
            );
        }
    }

    #[test]
    fn rest_port_maps_to_grpc_port() {
        for (url, port, want) in [
            ("http://qdrant:6333", None, "http://qdrant:6334"),
            ("http://qdrant", None, "http://qdrant:6334"),
            ("http://qdrant:6333/", None, "http://qdrant:6334"),
            ("http://qdrant:7000", None, "http://qdrant:7000"),
            ("http://qdrant:6333", Some(7334), "http://qdrant:7334"),
            ("http://[::1]:6333", None, "http://[::1]:6334"),
            ("http://[::1]", None, "http://[::1]:6334"),
            (
                "https://u:p@h.io:6333/base",
                None,
                "https://u:p@h.io:6334/base",
            ),
        ] {
            assert_eq!(grpc_url(url, port).unwrap(), want, "{url}");
        }
        for url in ["ftp://h", "http://", "http://h:port", "h:6333"] {
            assert!(grpc_url(url, None).is_err(), "{url}");
        }
    }

    #[test]
    fn grpc_port_override_from_env() {
        let s = settings(&[
            ("QDRANT_URL", "http://q:6333"),
            ("QDRANT_GRPC_PORT", "16334"),
        ])
        .unwrap();
        assert_eq!(s.grpc_url, "http://q:16334");
    }
}
