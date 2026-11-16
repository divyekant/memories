"""Command line of the parity kit.

  python -m parity capture --base-url URL [--out FILE]     run the scenario, record the replies
  python -m parity replay  --base-url URL --input FILE --out FILE
  python -m parity diff    LEFT RIGHT                     exit 1 if a reply differs
"""

import argparse
import json
import os
import sys

from parity import kit, scenario


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m parity")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("capture", "replay"):
        s = sub.add_parser(name)
        s.add_argument("--base-url", required=True)
        s.add_argument("--admin-key", default=os.getenv("PARITY_ADMIN_KEY", kit.ADMIN_KEY_DEFAULT))
        s.add_argument("--out", default=str(kit.GOLDEN_PATH) if name == "capture" else None, required=name == "replay")
        s.add_argument("--allow-nonempty", action="store_true",
                       help="run on a server that holds records (the scenario deletes records)")
        if name == "replay":
            s.add_argument("--input", default=str(kit.GOLDEN_PATH))
    d = sub.add_parser("diff")
    d.add_argument("left")
    d.add_argument("right")
    args = p.parse_args(argv)

    if args.cmd == "diff":
        problems = kit.diff_captures(kit.load(args.left), kit.load(args.right))
        for prob in problems:
            print(json.dumps(prob, ensure_ascii=False))
        print(f"{len(problems)} case(s) differ", file=sys.stderr)
        return 1 if problems else 0

    cases = scenario.build() if args.cmd == "capture" else kit.load(args.input)
    try:
        records = kit.run(cases, args.base_url, admin_key=args.admin_key, allow_nonempty=args.allow_nonempty,
                          log=lambda m: print(m, file=sys.stderr))
    except kit.NotEmptyError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    kit.dump(records, args.out)
    print(f"wrote {len(records)} case(s) to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
