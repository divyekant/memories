"""Write seed_records.json: the stored records of the Python server (v5.19.0)
after the parity scenario up to its destructive cases.

Usage (from the repository root):
  PYTHON=.venv/bin/python parity/run_python_server.sh /tmp/seed-data 18011 &
  .venv/bin/python server/crates/memories-store/tests/fixtures/make_seed_records.py \
      http://127.0.0.1:18011 /tmp/seed-data
"""

import json
import sys
from pathlib import Path

from parity import kit, scenario

base_url, data_dir = sys.argv[1], Path(sys.argv[2])
cases = scenario.build()
stop = next(i for i, c in enumerate(cases) if c["name"].startswith("extract"))
kit.run(cases[:stop], base_url)
records = json.loads((data_dir / "metadata.json").read_text(encoding="utf-8"))
out = Path(__file__).with_name("seed_records.json")
out.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"{len(records)} records -> {out}")
