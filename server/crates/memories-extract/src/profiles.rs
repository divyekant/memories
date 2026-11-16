//! `extraction_profiles.ExtractionProfiles` of v5.19.0: the profile of each
//! source prefix in `DATA_DIR/extraction_profiles.json`, with the same file
//! format (the `_explicit_fields` list included), so that the Python server
//! reads a file that this server wrote.

use std::fmt;
use std::path::{Path, PathBuf};

use serde::de::{Deserialize, Deserializer, MapAccess, Visitor};
use serde_json::{Map, Value, json};

const EXPLICIT: &str = "_explicit_fields";

/// `DEFAULT_RULES`.
pub fn default_rules() -> Value {
    json!({
        "always_remember": [
            "Decisions and their rationale, including boundary conditions (until/unless/because/blocked on)",
            "Learnings: root causes, fixes, gotchas, and non-obvious behavior discovered while working",
            "Durable user or project preferences and conventions",
            "Deferred work: tasks explicitly postponed, blocked, or scheduled for later",
        ],
        "never_remember": [
            "Session narration or running commentary about what was just done, edited, run, or is about to be done",
            "Restatements of code, diffs, or file contents that already live in the repository",
            "Ephemeral task chatter: progress updates, test counts, build or test status, command output",
            "Recalled or previously injected memory text repeated back in the conversation",
        ],
    })
}

/// `DEFAULTS`.
pub fn defaults() -> Map<String, Value> {
    let Value::Object(m) = json!({
        "mode": "standard",
        "max_facts": 30,
        "max_fact_chars": 500,
        "half_life_days": 30,
        "single_call": false,
        "enabled": true,
        "rules": default_rules(),
        "ttl_days": null,
        "confidence_threshold": null,
        "min_age_days": null,
        "confidence_half_life_days": null,
    }) else {
        unreachable!()
    };
    m
}

/// A JSON object with the order of its keys (the file keeps the order of the puts).
struct Ordered(Vec<(String, Value)>);

impl<'de> Deserialize<'de> for Ordered {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Ordered;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a JSON object")
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> Result<Ordered, A::Error> {
                let mut out: Vec<(String, Value)> = Vec::new();
                while let Some((k, v)) = a.next_entry::<String, Value>()? {
                    match out.iter_mut().find(|(x, _)| *x == k) {
                        Some(slot) => slot.1 = v,
                        None => out.push((k, v)),
                    }
                }
                Ok(Ordered(out))
            }
        }
        d.deserialize_map(V)
    }
}

pub struct ExtractionProfiles {
    path: PathBuf,
    profiles: Vec<(String, Map<String, Value>)>,
}

fn public(p: &Map<String, Value>) -> Map<String, Value> {
    let mut m = p.clone();
    m.remove(EXPLICIT);
    m
}

fn explicit(p: &Map<String, Value>) -> Vec<String> {
    p.get(EXPLICIT)
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

impl ExtractionProfiles {
    /// Reads the file if it exists. A file that does not parse is an error, as
    /// in Python (the server does not start), so that no put replaces it.
    pub fn open(path: &Path) -> Result<Self, String> {
        let profiles = match std::fs::read(path) {
            Ok(b) => {
                let Ordered(entries) =
                    serde_json::from_slice(&b).map_err(|e| format!("{}: {e}", path.display()))?;
                entries
                    .into_iter()
                    .map(|(k, v)| match v {
                        Value::Object(m) => Ok((k, m)),
                        _ => Err(format!("{}: profile {k} is not an object", path.display())),
                    })
                    .collect::<Result<_, _>>()?
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Vec::new(),
            Err(e) => return Err(format!("{}: {e}", path.display())),
        };
        Ok(Self {
            path: path.to_owned(),
            profiles,
        })
    }

    fn find(&self, prefix: &str) -> Option<&Map<String, Value>> {
        self.profiles
            .iter()
            .find(|(k, _)| k == prefix)
            .map(|(_, p)| p)
    }

    /// Writes the file: a new file, then a rename.
    fn save(&self) -> std::io::Result<()> {
        let mut out = String::from("{");
        for (i, (k, p)) in self.profiles.iter().enumerate() {
            let body = serde_json::to_string_pretty(p)?.replace('\n', "\n  ");
            out.push_str(if i == 0 { "\n  " } else { ",\n  " });
            out.push_str(&serde_json::to_string(k)?);
            out.push_str(": ");
            out.push_str(&body);
        }
        out.push_str(if self.profiles.is_empty() { "}" } else { "\n}" });
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, out)?;
        std::fs::rename(&tmp, &self.path)
    }

    /// `put`: defaults, then the old profile, then `config`. Returns the profile.
    pub fn put(
        &mut self,
        prefix: &str,
        config: &Map<String, Value>,
    ) -> std::io::Result<Map<String, Value>> {
        let existing = self.find(prefix).cloned().unwrap_or_default();
        let mut merged = defaults();
        merged.extend(public(&existing));
        merged.extend(config.clone());
        merged.insert("source_prefix".into(), json!(prefix));
        let mut fields: std::collections::BTreeSet<String> =
            explicit(&existing).into_iter().collect();
        fields.extend(config.keys().cloned());
        fields.insert("source_prefix".into());
        merged.insert(EXPLICIT.into(), json!(fields));
        match self.profiles.iter_mut().find(|(k, _)| k == prefix) {
            Some(slot) => slot.1 = merged.clone(),
            None => self.profiles.push((prefix.to_owned(), merged.clone())),
        }
        self.save()?;
        Ok(public(&merged))
    }

    pub fn get(&self, prefix: &str) -> Option<Map<String, Value>> {
        self.find(prefix).map(public)
    }

    pub fn delete(&mut self, prefix: &str) -> std::io::Result<bool> {
        let before = self.profiles.len();
        self.profiles.retain(|(k, _)| k != prefix);
        if self.profiles.len() == before {
            return Ok(false);
        }
        self.save()?;
        Ok(true)
    }

    pub fn list_all(&self) -> Vec<Map<String, Value>> {
        self.profiles.iter().map(|(_, p)| public(p)).collect()
    }

    /// `resolve`: the explicit fields of each matching profile, from the least
    /// to the most specific prefix, over the defaults. The most specific
    /// profile with explicit `rules` gives all of the rules.
    pub fn resolve(&self, source: &str) -> Map<String, Value> {
        let parts: Vec<&str> = source.split('/').collect();
        let matched: Vec<&Map<String, Value>> = (1..=parts.len())
            .rev()
            .filter_map(|i| self.find(&format!("{}/", parts[..i].join("/"))))
            .collect();
        let mut result = defaults();
        for p in matched.iter().rev() {
            for f in explicit(p) {
                if f != EXPLICIT
                    && f != "source_prefix"
                    && let Some(v) = p.get(&f)
                {
                    result.insert(f, v.clone());
                }
            }
        }
        let rules = matched
            .iter()
            .find(|p| explicit(p).iter().any(|f| f == "rules"))
            .and_then(|p| p.get("rules").cloned());
        result.insert("rules".into(), rules.unwrap_or_else(default_rules));
        result.insert("source_prefix".into(), json!(source));
        result
    }
}
