"""Every chooser on the two held-out sets, one table: eval/results/chooser_comparison.txt.

Tuning is frozen before this runs (Gate on cases_tune.jsonl; embedding temperature on the tune and
national dev sets). JEV is read from its committed cache only. OpenAI reads eval/.openai_cache.json
and, with --live, fetches only what is missing (aborting past OPENAI_LIVE_LIMIT requests).

Usage: backend/.venv/Scripts/python eval/compare_choosers.py [--live]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_resolver_eval import (CHOOSER_LABEL, COMPARISON_LABELS, EVAL, catalog_of, comparison_row,  # noqa: E402
                               evaluate, is_wrong_commit, load_set, make_client, make_hooks)
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.choosers import NAMES as CHOOSERS  # noqa: E402
from scheduling.embed_chooser import MODEL as EMBED_MODEL, TEMPERATURE  # noqa: E402
from scheduling.openai_chooser import MODEL as OPENAI_MODEL  # noqa: E402

SETS = (("national2", "held-out national2"), ("heldout2", "held-out SF (heldout2)"))
OUT = EVAL / "results" / "chooser_comparison.txt"
LABELS = COMPARISON_LABELS + ["failed requests"]


def row(m: dict) -> list[str]:
    return comparison_row(m) + [str(sum(1 for c in m["jev_calls"] if c.source == "failed"))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="fetch OpenAI answers missing from the cache")
    args = ap.parse_args()
    lines = [f"Chooser comparison on held-out sets. Gate act_p 0.8 / margin 0.6 / pair_p 0.85 (tuned on "
             f"cases_tune.jsonl). OpenAI {OPENAI_MODEL}; embeddings {EMBED_MODEL}, T={TEMPERATURE} (tuned on "
             f"cases_tune + cases_national).", "Latency: per model request, as measured when fetched (JEV and "
             "OpenAI: network round trip recorded in the cache; embeddings: local CPU).", ""]
    misses = []
    spent = {"requests": 0, "input": 0, "output": 0, "usd": 0.0}
    for name, label in SETS:
        cases = load_set(name)
        index = CatalogIndex.load(catalog_of(name))
        table = {}
        for chooser in CHOOSERS:
            client = make_client(chooser, live=args.live and chooser == "openai")
            if chooser == "embed":
                client.warm_up(index)
            hooks = make_hooks(index, client)
            try:
                m = evaluate(index, cases, hooks, client, latency_repeats=0)
            finally:
                if client:
                    client.save()
                    client.close()
            table[chooser] = row(m)
            if chooser == "openai":
                live = [c for c in client.calls if c.source == "live"]
                spent["requests"] += len(live)
                spent["input"] += sum(c.input_tokens for c in live)
                spent["output"] += sum(c.output_tokens for c in live)
                spent["usd"] += sum(c.usd for c in live)
            for case, turns in m["failures"]:
                for i, t in enumerate(turns):
                    if t["kind"] == "resolve" and t["errs"] and is_wrong_commit(t["plan"], t["errs"]):
                        misses.append(f"  {label} / {CHOOSER_LABEL[chooser]}: {case['id']} turn {i + 1}: "
                                      f"{'; '.join(t['errs'])}" + "".join(f"\n      {purpose}: {v.describe()}"
                                                                         for purpose, v in t["plan"].consults))
        lines.append(f"== {label}: {len(cases)} cases ==")
        lines.append(f"{'metric':<32}" + "".join(f"{CHOOSER_LABEL[c]:<30}" for c in table))
        for k, metric in enumerate(LABELS):
            lines.append(f"{metric:<32}" + "".join(f"{r[k]:<30}" for r in table.values()))
        lines.append("")
    lines.append("Wrong commits (every mode):")
    lines.extend(misses or ["  none"])
    lines.append("")
    lines.append(f"OpenAI requests sent this run: {spent['requests']} ({spent['input']} input + {spent['output']} "
                 f"output tokens, ${spent['usd']:.5f})")
    text = "\n".join(lines) + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
