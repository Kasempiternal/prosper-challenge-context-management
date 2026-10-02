"""Choose the embedding chooser's softmax temperature on the DEV sets only: eval/cases_tune.jsonl
(SF catalog) and eval/cases_national.jsonl (national catalog). The Gate stays JEV's.

Objective, in order, summed over both sets: fewest wrong commits, most turns fully correct, fewest
questions per booking. Ties go to the HIGHEST temperature: a flatter distribution acts less often.

Usage: backend/.venv/Scripts/python eval/tune_embed_temperature.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_resolver_eval import NATIONAL_CATALOG, SF_CATALOG, evaluate, load_set, make_hooks  # noqa: E402
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.embed_chooser import EmbedClient, shared_embedder, warm_catalog  # noqa: E402

GRID = [0.005, 0.0075, 0.01, 0.0125, 0.015, 0.0175, 0.02, 0.025, 0.03, 0.04, 0.05, 0.075, 0.1]
DEV = (("tune", SF_CATALOG), ("national", NATIONAL_CATALOG))


def main() -> None:
    embedder = shared_embedder()
    sets = []
    for name, path in DEV:
        index = CatalogIndex.load(path)
        warm_catalog(embedder, index)
        sets.append((name, index, load_set(name)))
    rows = []
    for temp in GRID:
        per = {}
        for name, index, cases in sets:
            client = EmbedClient(embedder, temperature=temp)
            per[name] = evaluate(index, cases, make_hooks(index, client), client, latency_repeats=0)
        wrong = sum(m["wrong_commits"] for m in per.values())
        correct = sum(m["correct"] for m in per.values())
        qpb = sum(m["qpb"] * m["bookings"] for m in per.values()) / max(sum(m["bookings"] for m in per.values()), 1)
        rows.append(((wrong, -correct, round(qpb, 4), -temp), temp, per))
    print(f"{'T':>7}  " + "  ".join(f"{n:<28}" for n, _ in DEV) + "  total")
    for key, temp, per in rows:
        cells = "  ".join(f"wc {m['wrong_commits']}/{m['commits']} top-1 {m['correct']}/{m['evaluated']} "
                          f"q {m['qpb']:.2f}".ljust(28) for m in per.values())
        print(f"{temp:>7}  {cells}  wc {key[0]} top-1 {-key[1]} q {key[2]:.2f}")
    best = min(rows, key=lambda r: r[0])
    print(f"\nchosen: T={best[1]}")


if __name__ == "__main__":
    main()
