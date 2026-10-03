"""One dev case, turn by turn: what the resolver said, its notes and the model requests behind them.

  backend/.venv/Scripts/python eval/explain_case.py SET CASE_ID [--chooser jev|none|openai|embed]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_resolver_eval as E  # noqa: E402

from scheduling.catalog_index import CatalogIndex  # noqa: E402
from sets import SETS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("set", choices=[n for n, s in SETS.items() if not s.blind])
    ap.add_argument("case_ids", nargs="+")
    ap.add_argument("--chooser", default="jev")
    args = ap.parse_args()
    index = CatalogIndex.load(E.catalog_of(args.set))
    client = E.make_client(args.chooser) if args.chooser != "none" else None
    hooks = E.make_hooks(index, client)
    cases = {c["id"]: c for c in E.load_set(args.set)}
    try:
        for cid in args.case_ids:
            case = cases[cid]
            print(f"== {cid} {case.get('patient')} expected {case['expected']}")
            for i, t in enumerate(E.run_case(index, case, hooks, client=client)):
                turn = case["turns"][i]
                print(f"  t{i + 1} {turn.get('update') or turn.get('lookup')}")
                if t["kind"] != "resolve":
                    print(f"     facts {t['facts']} errs {t['errs']}")
                    continue
                plan = t["plan"]
                print(f"     {plan.status}: {plan.say}")
                print(f"     errs {t['errs']}")
                for n in plan.notes:
                    print(f"     . {n}")
                for c in t["calls"]:
                    print(f"     > {c.purpose} {c.source} p={c.p}")
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    main()
