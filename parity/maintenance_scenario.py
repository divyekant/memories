"""The maintenance scenario (task P5-3), in two parts.

Part `maintenance-seed` writes the seed corpus. Between the two parts, the exit
script sets old dates on some records and writes `extraction_profiles.json`.
Part `maintenance` then runs each job: dry runs first, then the changes. The
case `inv-N` lists the IDs of each record that is not pinned, so the diff of
two captures shows each difference in the set of deleted records. The replies
of `enforce-policies` list the archived records.
"""

from parity import seed
from parity.scenario import c


def _inventory(n):
    return c(f"inv-{n}", "POST", "/memory/delete-by-source", json={"source_pattern": "/", "dry_run": True})


def build_seed():
    return seed.build()


def build():
    return [
        _inventory(0),
        c("compact-95", "POST", "/maintenance/compact", json={"threshold": 0.95}),
        c("compact-85", "POST", "/maintenance/compact", json={}),
        c("enforce-dry", "POST", "/maintenance/enforce-policies"),
        c("enforce", "POST", "/maintenance/enforce-policies", query="dry_run=false"),
        c("enforce-after", "POST", "/maintenance/enforce-policies", query="dry_run=true"),
        c("prune-dry", "POST", "/maintenance/prune"),
        c("prune", "POST", "/maintenance/prune", query="dry_run=false"),
        _inventory(1),
        c("consolidate-dry-wip", "POST", "/maintenance/consolidate", query="dry_run=true&source_prefix=wip/"),
        c("consolidate-dry", "POST", "/maintenance/consolidate"),
        c("consolidate", "POST", "/maintenance/consolidate", query="dry_run=false"),
        _inventory(2),
        c("consolidate-again", "POST", "/maintenance/consolidate", query="dry_run=true"),
        c("reembed", "POST", "/maintenance/reembed", json={}),
        c("compact-after", "POST", "/maintenance/compact", json={"threshold": 0.95}),
        _inventory(3),
    ]
