//! The source scope of a search: `_build_source_filter`,
//! `_source_matches_scope` and `source_matches_prefixes` of v5.19.0.

/// `auth_context.source_matches_prefixes`. `None` is unrestricted. A prefix
/// matches its exact source and each source under it, never `prefix-other`.
pub fn source_matches_prefixes(source: &str, prefixes: Option<&[String]>) -> bool {
    let Some(prefixes) = prefixes else {
        return true;
    };
    if source.split('/').any(|part| part == "..") {
        return false;
    }
    prefixes.iter().any(|p| {
        // Python: pfx.rstrip("/").removesuffix("/*").rstrip("/")
        let p = p.trim_end_matches('/');
        let base = p.strip_suffix("/*").unwrap_or(p).trim_end_matches('/');
        source == base
            || source
                .strip_prefix(base)
                .is_some_and(|rest| rest.starts_with('/'))
    })
}

/// `MemoryEngine._source_matches_scope`.
pub fn source_matches_scope(source: &str, prefix: &str, boundary: bool) -> bool {
    if boundary {
        source == prefix
            || source
                .strip_prefix(prefix)
                .is_some_and(|r| r.starts_with('/'))
    } else {
        source.starts_with(prefix)
    }
}

/// `project_memory.is_reserved_namespace_source`.
pub fn is_reserved_source(source: &str) -> bool {
    source.starts_with("project/") || source.starts_with("person/")
}

/// The scope arguments of `search`, `hybrid_search` and `is_novel`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Scope {
    /// `source_prefix`. `Some("")` lists all sources in the filter, as Python does.
    pub source_prefix: Option<String>,
    pub source_exact: Option<String>,
    pub source_boundary: bool,
    /// `allowed_prefixes` (the `source_prefixes` field of a search).
    pub allowed_prefixes: Option<Vec<String>>,
    pub exclude_reserved: bool,
    pub include_archived: bool,
}

impl Scope {
    fn prefix(&self) -> Option<&str> {
        self.source_prefix.as_deref().filter(|p| !p.is_empty())
    }

    fn uses_sources(&self) -> bool {
        self.source_prefix.is_some()
            || self.source_exact.is_some()
            || self.allowed_prefixes.is_some()
            || self.exclude_reserved
    }

    /// The sources for the Qdrant filter, from all distinct `sources` (sorted).
    /// `None` when no source filter applies. An empty list matches no record.
    pub fn filter_sources<'a>(
        &self,
        sources: impl Iterator<Item = &'a str>,
    ) -> Option<Vec<String>> {
        if !self.uses_sources() {
            return None;
        }
        let allowed = self.allowed_prefixes.as_deref();
        Some(
            sources
                .filter(|s| match &self.source_exact {
                    Some(exact) => s == exact && self.prefix().is_none_or(|p| s.starts_with(p)),
                    None => self
                        .prefix()
                        .is_none_or(|p| source_matches_scope(s, p, self.source_boundary)),
                })
                .filter(|s| allowed.is_none() || source_matches_prefixes(s, allowed))
                .filter(|s| !self.exclude_reserved || !is_reserved_source(s))
                .map(str::to_owned)
                .collect(),
        )
    }

    /// The check of `search` on each vector hit, after the Qdrant filter.
    pub fn admits_hit(&self, source: &str) -> bool {
        self.prefix()
            .is_none_or(|p| source_matches_scope(source, p, self.source_boundary))
            && self.source_exact.as_deref().is_none_or(|e| source == e)
            && source_matches_prefixes(source, self.allowed_prefixes.as_deref())
            && !(self.exclude_reserved && is_reserved_source(source))
    }

    /// True when `_visible` and `_filter_adjacency` can drop a record.
    pub fn limits_visibility(&self) -> bool {
        self.source_exact.is_some()
            || self.prefix().is_some()
            || self.allowed_prefixes.is_some()
            || !self.include_archived
    }

    /// `hybrid_search._visible` and the test of `_filter_adjacency`.
    pub fn visible(&self, source: &str, archived: bool) -> bool {
        self.source_exact.as_deref().is_none_or(|e| source == e)
            && self
                .prefix()
                .is_none_or(|p| source_matches_scope(source, p, self.source_boundary))
            && source_matches_prefixes(source, self.allowed_prefixes.as_deref())
            && (self.include_archived || !archived)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    const SOURCES: &[&str] = &["", "codex/a", "codex/ab", "codex/a/b", "project/x", "wip/a"];

    fn sources(scope: &Scope) -> Option<Vec<String>> {
        scope.filter_sources(SOURCES.iter().copied())
    }

    #[test]
    fn prefixes_follow_python() {
        let p = s(&["codex/a/*"]);
        assert!(source_matches_prefixes("codex/a", Some(&p)));
        assert!(source_matches_prefixes("codex/a/b", Some(&p)));
        assert!(!source_matches_prefixes("codex/ab", Some(&p)));
        assert!(!source_matches_prefixes("codex/a/../x", Some(&p)));
        assert!(source_matches_prefixes("anything", None));
    }

    #[test]
    fn filter_sources_follow_build_source_filter() {
        assert_eq!(sources(&Scope::default()), None);
        let prefix = |p: &str, boundary| Scope {
            source_prefix: Some(p.into()),
            source_boundary: boundary,
            ..Default::default()
        };
        assert_eq!(
            sources(&prefix("codex/a", false)),
            Some(s(&["codex/a", "codex/ab", "codex/a/b"]))
        );
        assert_eq!(
            sources(&prefix("codex/a", true)),
            Some(s(&["codex/a", "codex/a/b"]))
        );
        // An empty prefix lists each source.
        assert_eq!(sources(&prefix("", false)).unwrap().len(), SOURCES.len());
        let exact = Scope {
            source_exact: Some("codex/ab".into()),
            source_prefix: Some("codex/a/".into()),
            ..Default::default()
        };
        assert_eq!(sources(&exact), Some(vec![]));
        let allowed = Scope {
            allowed_prefixes: Some(s(&["codex/a", "project"])),
            exclude_reserved: true,
            ..Default::default()
        };
        assert_eq!(sources(&allowed), Some(s(&["codex/a", "codex/a/b"])));
    }

    #[test]
    fn visible_checks_archive_and_scope() {
        let scope = Scope {
            source_prefix: Some("codex/".into()),
            ..Default::default()
        };
        assert!(scope.visible("codex/a", false));
        assert!(!scope.visible("codex/a", true));
        assert!(!scope.visible("wip/a", false));
        assert!(scope.limits_visibility());
        let all = Scope {
            include_archived: true,
            source_prefix: Some(String::new()),
            ..Default::default()
        };
        assert!(!all.limits_visibility());
        assert!(all.admits_hit("x"));
    }
}
