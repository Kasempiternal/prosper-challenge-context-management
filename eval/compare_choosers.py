"""Every chooser on the two held-out sets, one table: eval/results/chooser_comparison.txt.

Tuning is frozen before this runs (Gate on cases_tune.jsonl; embedding temperature on the tune and
national dev sets). JEV is read from its committed cache only. OpenAI reads eval/.openai_cache.json
and, with --live, fetches only what is missing (aborting past OPENAI_LIVE_LIMIT requests).

Usage: backend/.venv/Scripts/python eval/compare_choosers.py [--live]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_resolver_eval import (CHOOSER_LABEL, EVAL, NATIONAL_CATALOG, SF_CATALOG, evaluate, is_wrong_commit,  # noqa: E402
                               load_set, make_client, make_hooks, pct, run_case)
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.embed_chooser import MODEL as EMBED_MODEL, TEMPERATURE  # noqa: E402
from scheduling.openai_chooser import MODEL as OPENAI_MODEL  # noqa: E402

SETS = (("national2", NATIONAL_CATALOG, "held-out national2"), ("heldout2", SF_CATALOG, "held-out SF (heldout2)"))
CHOOSERS = ("jev", "openai", "embed", "none")
OUT = EVAL / "results" / "chooser_comparison.txt"


def row(m: dict) -> dict:
    calls = m["jev_calls"]
    ms = [c.latency_ms for c in calls if c.source in ("live", "cache")]
    usd = sum(c.usd for c in calls)
    return {
        "wrong-commit": f"{m['wrong_commits']}/{m['commits']} ({m['wrong_commits'] / max(m['commits'], 1):.1%})",
        "top-1": f"{m['correct']}/{m['evaluated']} ({m['correct'] / max(m['evaluated'], 1):.1%})",
        "q/booking": f"{m['qpb']:.2f}",
        "model rate": f"{len(calls) / max(m['all_turns'], 1):.0%} ({len(calls)})",
        "p50/p95 ms": f"{pct(ms, 50):.0f}/{pct(ms, 95):.0f}" if ms else "-",
        "$/1k turns": f"${usd / max(m['all_turns'], 1) * 1000:.4f}",
        "failed": sum(1 for c in calls if c.source == "failed"),
    }


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
    for name, catalog, label in SETS:
        if name == "national2":
            pinned = {c.get("catalog_sha256") for c in load_set(name)}
            assert pinned == {hashlib.sha256(catalog.read_bytes()).hexdigest()}, "national catalog changed"
        index = CatalogIndex.load(catalog)
        cases = load_set(name)
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
                        notes = [n for n in t["plan"].notes if "disambiguator" in n or "chooser" in n]
                        misses.append(f"  {label} / {CHOOSER_LABEL[chooser]}: {case['id']} turn {i + 1}: "
                                      f"{'; '.join(t['errs'])}" + "".join(f"\n      {n}" for n in notes))
        cols = list(next(iter(table.values())))
        lines.append(f"== {label}: {len(cases)} cases ==")
        lines.append(f"{'mode':<12}" + "".join(f"{c:<18}" for c in cols))
        for chooser, r in table.items():
            lines.append(f"{CHOOSER_LABEL[chooser]:<12}" + "".join(f"{str(r[c]):<18}" for c in cols))
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
