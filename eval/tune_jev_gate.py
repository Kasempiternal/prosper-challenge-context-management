"""Choose Gate(act_p, margin, pair_p) on eval/cases_tune.jsonl ONLY, from cached JEV answers.

Objective, in order: fewest wrong commits, most turns fully correct, fewest questions per booking.
Ties go to the highest act_p, then the highest margin (a wrong commit is the costly error), then
the LOWEST pair_p: both pair and open are questions, and a targeted either/or is the better one.
Run eval/run_resolver_eval.py --set tune --jev on --live first to fill the cache.

Usage: backend/.venv/Scripts/python eval/tune_jev_gate.py
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_resolver_eval import CACHE, ROOT, evaluate, load_set, make_hooks  # noqa: E402
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.decision import Gate  # noqa: E402
from scheduling.jev import JevClient  # noqa: E402

ACT = [round(0.5 + 0.05 * i, 2) for i in range(10)] + [0.99]
MARGIN = [round(0.1 * i, 1) for i in range(10)]
PAIR = [round(0.5 + 0.05 * i, 2) for i in range(10)] + [0.99]


def main() -> None:
    index = CatalogIndex.load(ROOT / "backend" / "data" / "catalog.json")
    cases = load_set("tune")
    client = JevClient.from_env(mode="cache", cache_path=CACHE)
    scored = []
    for act_p, margin, pair_p in itertools.product(ACT, MARGIN, PAIR):
        m = evaluate(index, cases, make_hooks(index, client, Gate(act_p, margin, pair_p)), client, latency_repeats=0)
        failed = sum(1 for c in m["jev_calls"] if c.source == "failed")
        if failed:
            raise SystemExit(f"{failed} tune requests missing from the cache; run the live tune eval first")
        key = (m["wrong_commits"], -m["correct"], m["qpb"], -act_p, -margin, pair_p)
        scored.append((key, (act_p, margin, pair_p), m))
    scored.sort(key=lambda s: s[0])
    print(f"grid: {len(scored)} gates over {len(cases)} tune cases")
    print(f"{'act_p':>6} {'margin':>6} {'pair_p':>6}  wrong-commit  top-1   q/booking")
    for key, (a, mg, p), m in scored[:12]:
        print(f"{a:>6} {mg:>6} {p:>6}  {m['wrong_commits']:>12}  {m['correct']:>2}/{m['evaluated']}  {m['qpb']:.2f}")
    best = scored[0]
    plateau = [g for k, g, _ in scored if k[:3] == best[0][:3]]
    print(f"\nchosen: act_p={best[1][0]} margin={best[1][1]} pair_p={best[1][2]}  "
          f"({len(plateau)} gates tie on the objective)")
    for name, ix in (("act_p", 0), ("margin", 1), ("pair_p", 2)):
        vals = sorted({g[ix] for g in plateau})
        print(f"  plateau {name}: {vals[0]} .. {vals[-1]}")


if __name__ == "__main__":
    main()
