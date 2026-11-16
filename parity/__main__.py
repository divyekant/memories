"""Command line of the parity kit.

  python -m parity capture --base-url URL [--out FILE] [--scenario main|search|bulk]
                                                         run the scenario, record the replies
  python -m parity replay  --base-url URL --input FILE --out FILE
  python -m parity diff    LEFT RIGHT                     exit 1 if a reply differs

  --only NAME,NAME (replay, diff) uses only the cases with these names.
  --var NAME=VALUE (replay) sets a variable that an earlier case would save, for example key:ro.
"""

import argparse
import json
import os
import sys

from parity import bulk_scenario, kit, scenario, search_scenario

SCENARIOS = {
    "main": scenario.build,
    "search": lambda: scenario.fill(search_scenario.build()),
    "bulk": bulk_scenario.build,
}


def _names(text):
    return [n.strip() for n in text.split(",") if n.strip()]


def _var(text):
    name, sep, value = text.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError("use NAME=VALUE")
    return name, value


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
        if name == "capture":
            s.add_argument("--scenario", choices=sorted(SCENARIOS), default="main")
        if name == "replay":
            s.add_argument("--input", default=str(kit.GOLDEN_PATH))
            s.add_argument("--only", type=_names)
            s.add_argument("--var", action="append", default=[], type=_var)
    d = sub.add_parser("diff")
    d.add_argument("left")
    d.add_argument("right")
    d.add_argument("--only", type=_names)
    args = p.parse_args(argv)

    if args.cmd == "diff":
        left = kit.select(kit.load(args.left), args.only)
        right = kit.select(kit.load(args.right), args.only)
        problems = kit.diff_captures(left, right)
        for prob in problems:
            print(json.dumps(prob, ensure_ascii=False))
        print(f"{len(problems)} case(s) differ", file=sys.stderr)
        return 1 if problems else 0

    if args.cmd == "capture":
        cases, extra_vars = SCENARIOS[args.scenario](), {}
    else:
        cases, extra_vars = kit.select(kit.load(args.input), args.only), dict(args.var)
    try:
        records = kit.run(cases, args.base_url, admin_key=args.admin_key, allow_nonempty=args.allow_nonempty,
                          log=lambda m: print(m, file=sys.stderr), extra_vars=extra_vars)
    except kit.NotEmptyError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    kit.dump(records, args.out)
    print(f"wrote {len(records)} case(s) to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
