"""Compare two `GET /export` files (task P5-4 exit check).

Each file must have a header whose count equals its record lines. The two
files must hold the same records: the same text, source and custom fields,
the links included. The import markers `imported` and `import_source` and
the created and updated times are not compared, because an import writes new
values for them.

Usage: python3 compare_exports.py <a.ndjson> <b.ndjson> [--exact]
With --exact, the times and the import markers must be equal too (a backup
and a restore of one server).
"""

import json
import sys
from collections import Counter

MARKERS = {"imported", "import_source"}


def load(path):
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    header, records = lines[0], lines[1:]
    if not header.get("_header") or header.get("count") != len(records):
        sys.exit(f"{path}: the header count {header.get('count')} is not the {len(records)} records")
    return records


def key(record, exact):
    if exact:
        return json.dumps(record, sort_keys=True)
    custom = {k: v for k, v in record["custom_fields"].items() if k not in MARKERS}
    return json.dumps([record["text"], record["source"], custom], sort_keys=True)


def main():
    a, b = sys.argv[1], sys.argv[2]
    exact = "--exact" in sys.argv[3:]
    ra, rb = load(a), load(b)
    ca = Counter(key(r, exact) for r in ra)
    cb = Counter(key(r, exact) for r in rb)
    if ca != cb:
        only_a = list((ca - cb).elements())[:5]
        only_b = list((cb - ca).elements())[:5]
        sys.exit(f"the records differ\nonly in {a}: {only_a}\nonly in {b}: {only_b}")
    links = sum(len(r["custom_fields"].get("links") or []) for r in ra)
    print(f"verify passed: {len(ra)} records, {links} links")


if __name__ == "__main__":
    main()
