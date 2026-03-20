"""Extraction profiles — per-source extraction configuration with cascade resolution."""

import json
import os
from typing import Optional

DEFAULTS = {
    "mode": "standard",
    "max_facts": 30,
    "max_fact_chars": 500,
    "half_life_days": 30,
    "single_call": False,
    "enabled": True,
    "rules": {},
}

# Internal key used to track which fields were explicitly set by the user.
_EXPLICIT_KEY = "_explicit_fields"


class ExtractionProfiles:
    def __init__(self, path: str):
        self._path = path
        self._profiles: dict[str, dict] = {}
        if os.path.exists(path):
            with open(path) as f:
                self._profiles = json.load(f)

    def _save(self):
        with open(self._path, "w") as f:
            json.dump(self._profiles, f, indent=2)

    def _public(self, profile: dict) -> dict:
        """Return profile without internal bookkeeping fields."""
        return {k: v for k, v in profile.items() if k != _EXPLICIT_KEY}

    def put(self, source_prefix: str, config: dict) -> dict:
        existing = self._profiles.get(source_prefix, {})
        # Merge: defaults → existing → new config.
        merged = {**DEFAULTS, **self._public(existing), **config, "source_prefix": source_prefix}
        # Track which fields were explicitly provided (across all puts).
        prev_explicit: set = set(existing.get(_EXPLICIT_KEY, []))
        # Fields from config are explicit; source_prefix is always explicit.
        new_explicit = prev_explicit | set(config.keys()) | {"source_prefix"}
        merged[_EXPLICIT_KEY] = sorted(new_explicit)
        self._profiles[source_prefix] = merged
        self._save()
        return self._public(merged)

    def get(self, source_prefix: str) -> Optional[dict]:
        profile = self._profiles.get(source_prefix)
        return self._public(profile) if profile is not None else None

    def delete(self, source_prefix: str) -> bool:
        if source_prefix not in self._profiles:
            return False
        del self._profiles[source_prefix]
        self._save()
        return True

    def list_all(self) -> list[dict]:
        return [self._public(p) for p in self._profiles.values()]

    def resolve(self, source: str) -> dict:
        """Cascade from most-specific prefix to least-specific, then DEFAULTS.

        Only explicitly-set fields from a profile participate in the overlay.
        Rules are NOT merged — the most-specific profile that explicitly set
        rules wins; it fully replaces any parent rules.
        """
        # Build candidate prefixes from most specific to least specific.
        parts = source.split("/")
        candidates: list[str] = []
        for i in range(len(parts), 0, -1):
            prefix = "/".join(parts[:i]) + "/"
            candidates.append(prefix)

        # Collect matching profiles in order (most specific first).
        matched: list[dict] = []
        for prefix in candidates:
            if prefix in self._profiles:
                matched.append(self._profiles[prefix])

        if not matched:
            return {**DEFAULTS, "source_prefix": source}

        # Build result starting from DEFAULTS, then overlay from least to most specific
        # using only explicitly-set fields.
        result = dict(DEFAULTS)
        for profile in reversed(matched):  # least specific first
            explicit: set = set(profile.get(_EXPLICIT_KEY, []))
            for field in explicit:
                if field in profile and field not in (_EXPLICIT_KEY, "source_prefix"):
                    result[field] = profile[field]

        # Rules cascade: child fully replaces — use the most-specific explicit rules found.
        rules_resolved = False
        for profile in matched:  # most specific first
            explicit: set = set(profile.get(_EXPLICIT_KEY, []))
            if "rules" in explicit:
                result["rules"] = profile["rules"]
                rules_resolved = True
                break

        if not rules_resolved:
            result["rules"] = DEFAULTS["rules"]

        result["source_prefix"] = source
        return result
