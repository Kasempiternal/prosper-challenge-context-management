"""Offline resolver eval: replays case files through merge + resolve.

Usage:
  backend/.venv/Scripts/python eval/run_resolver_eval.py [--set main|heldout|heldout2|tune|all|national]
                                                        [--catalog PATH] [--jev off|on] [--live] [--verbose]

--set national  eval/cases_national.jsonl against the national catalog. Refuses to run unless the
           catalog's sha256 equals the one pinned in every case. `all` stays the SF sets.

--jev off  no model; no network.
--jev on   JEV answers come from eval/.jev_cache.json (offline, deterministic). The no-JEV run is
           repeated alongside it and printed side by side.
--live     with --jev on: send every distinct JEV request to the network, refresh the cache and
           report the measured latency. Costs money (~$0.0001 per request).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from scheduling.availability import MockAvailability  # noqa: E402
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.decision import Gate  # noqa: E402
from scheduling.jev import USD_PER_INPUT_TOKEN, JevClient, JevProviderChooser, JevSiteChooser, JevTypeDisambiguator  # noqa: E402
from scheduling.lookup import lookup  # noqa: E402
from scheduling.request import Request, Update, merge  # noqa: E402
from scheduling.resolver import Plan, plan_json, resolve  # noqa: E402

EVAL = ROOT / "eval"
CACHE = EVAL / ".jev_cache.json"
SETS = ("main", "heldout", "heldout2", "tune")
SF_CATALOG = ROOT / "backend" / "data" / "catalog.json"
NATIONAL_CATALOG = ROOT / "backend" / "data" / "national" / "catalog.json"
LATENCY_REPEATS = 20
ENC = tiktoken.get_encoding("o200k_base")  # gpt-4o / gpt-4.1 tokenizer
COMMIT = {"offer", "confirm"}


def load_set(name: str) -> list[dict]:
    path = {"heldout2": EVAL / "cases_heldout2.jsonl", "tune": EVAL / "cases_tune.jsonl",
            "national": EVAL / "cases_national.jsonl"}.get(name, EVAL / "cases.jsonl")
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if name == "main":
        return [c for c in cases if c["category"] != "heldout"]
    if name == "heldout":
        return [c for c in cases if c["category"] == "heldout"]
    return cases


def tokens(text: str) -> int:
    return len(ENC.encode(text))


def miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def check_plan(plan: Plan, exp: dict, index=None) -> list[str]:
    """Return mismatch descriptions; empty means the turn is correct.

    National keys: `types_any` and `location_ids_subset` bound every offer (and, on a refusal, every
    suggested alternative, of which there must be one); `max_miles` from `anchor` [lat, lon];
    `refuse_reason` is the refusal code."""
    errs = []
    if plan.status != exp["status"]:
        errs.append(f"status {plan.status} != {exp['status']}")
    picks = list(plan.offers) + ([plan.confirm] if plan.confirm else [])
    if "type_id" in exp and any(o.type_id != exp["type_id"] for o in picks):
        errs.append(f"types {sorted({o.type_id for o in picks})} != {exp['type_id']}")
    if "provider_ids" in exp and any(o.provider_id not in exp["provider_ids"] for o in picks):
        errs.append(f"providers {sorted({o.provider_id for o in picks})} not within {exp['provider_ids']}")
    if "location_ids" in exp and any(o.location_id not in exp["location_ids"] for o in picks):
        errs.append(f"locations {sorted({o.location_id for o in picks})} not within {exp['location_ids']}")
    if "weekday" in exp and any(o.start.weekday() != exp["weekday"] for o in picks):
        errs.append("offered a day outside the requested weekday")
    if exp.get("part_of_day") == "morning" and any(o.start.hour >= 12 for o in picks):
        errs.append("offered an afternoon time for a morning request")
    if "ask_field" in exp and (plan.ask is None or plan.ask.field != exp["ask_field"]):
        errs.append(f"ask {plan.ask.field if plan.ask else None} != {exp['ask_field']}")
    if "ask_options" in exp and (plan.ask is None or list(plan.ask.options) != exp["ask_options"]):
        errs.append(f"ask options {list(plan.ask.options) if plan.ask else None} != {exp['ask_options']}")
    if "refuse_code" in exp and (plan.refusal is None or plan.refusal.code != exp["refuse_code"]):
        errs.append(f"refusal {plan.refusal.code if plan.refusal else None} != {exp['refuse_code']}")
    if "alternatives" in exp:
        got = {tuple(a) for a in plan.refusal.alternatives} if plan.refusal else set()
        if got != {tuple(a) for a in exp["alternatives"]}:
            errs.append(f"alternatives {sorted(got)} != {exp['alternatives']}")
    if "types_any" in exp and any(o.type_id not in exp["types_any"] for o in picks):
        errs.append(f"types {sorted({o.type_id for o in picks})} not within {exp['types_any']}")
    if "location_ids_subset" in exp:
        if plan.status == "refuse":
            alt_locs = {a[2] for a in plan.refusal.alternatives} if plan.refusal else set()
            if not alt_locs or not alt_locs <= set(exp["location_ids_subset"]):
                errs.append(f"alternative locations {sorted(alt_locs, key=str)} not within {exp['location_ids_subset']}")
        elif any(o.location_id not in exp["location_ids_subset"] for o in picks):
            errs.append(f"locations {sorted({o.location_id for o in picks})} not within {exp['location_ids_subset']}")
    if "max_miles" in exp:
        far = sorted({o.location_id for o in picks
                      if miles(tuple(exp["anchor"]), (index.locations[o.location_id].lat,
                                                      index.locations[o.location_id].lon)) > exp["max_miles"]})
        if far:
            errs.append(f"locations {far} farther than {exp['max_miles']} mi")
    if "refuse_reason" in exp and (plan.refusal is None or plan.refusal.code != exp["refuse_reason"]):
        errs.append(f"refusal {plan.refusal.code if plan.refusal else None} != {exp['refuse_reason']}")
    if exp["status"] not in COMMIT and plan.status in COMMIT:
        errs.append("WRONG COMMIT: offered when it should not")
    return errs


def is_wrong_commit(plan: Plan, errs: list[str]) -> bool:
    return plan.status in COMMIT and bool(errs)


def run_case(index, case: dict, hooks: dict, timings: list[float] | None = None):
    av = MockAvailability(index)
    req = Request()
    turns = []
    for i, turn in enumerate(case["turns"]):
        last = i == len(case["turns"]) - 1
        exp = case["expected"] if last else turn.get("expect")
        if "lookup" in turn:
            facts = lookup(index, turn["lookup"]["kind"], turn["lookup"]["phrase"])
            ok = any(exp["facts_contain"] in f for f in facts)
            turns.append({"kind": "lookup", "facts": facts, "errs": [] if ok else [f"missing {exp['facts_contain']!r}"],
                          "exp": exp})
            continue
        args = dict(turn["update"])
        if i == 0:
            args = {**case.get("patient", {}), **args}
        req = merge(req, Update.from_args(args))
        t0 = time.perf_counter()
        plan = resolve(index, req, av, **hooks)
        if timings is not None:
            timings.append((time.perf_counter() - t0) * 1000)
        req = plan.req
        turns.append({"kind": "resolve", "plan": plan, "exp": exp, "errs": check_plan(plan, exp, index) if exp else None})
    return turns


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def make_hooks(index, client: JevClient | None, gate: Gate = Gate()) -> dict:
    if client is None:
        return {}
    return {"disambiguator": JevTypeDisambiguator(index, client, gate), "chooser": JevProviderChooser(index, client, gate),
            "site_chooser": JevSiteChooser(index, client, gate)}


def evaluate(index, cases: list[dict], hooks: dict, client: JevClient | None, latency_repeats: int = LATENCY_REPEATS) -> dict:
    evaluated = correct = wrong_commits = commits = resolve_turns = 0
    asks_made = asks_expected = asks_tp = 0
    refusals_expected = refusals_right = 0
    questions_per_booking = []
    say_tok, summary_tok, result_tok, direct_tok = [], [], [], []
    by_cat: dict[str, list[bool]] = {}
    failures = []
    n_calls_before = len(client.calls) if client else 0
    jev_turns = 0

    for case in cases:
        turns = run_case(index, case, hooks)
        case_ok = True
        for t in turns:
            if t["kind"] == "resolve":
                plan = t["plan"]
                jev_turns += any("disambiguator:" in n or "chooser" in n for n in plan.notes)
                say_tok.append(tokens(plan.say))
                summary_tok.append(tokens(plan.summary))
                result_tok.append(tokens(plan_json(plan)))
                direct_tok.append(tokens(plan_json(plan, speak_direct=True)))
            if t["errs"] is None:
                continue
            evaluated += 1
            ok = not t["errs"]
            correct += ok
            case_ok &= ok
            if t["kind"] == "lookup":
                continue
            resolve_turns += 1
            plan, exp = t["plan"], t["exp"]
            commits += plan.status in COMMIT
            wrong_commits += is_wrong_commit(plan, t["errs"])
            predicted_ask = plan.status == "ask"
            expected_ask = exp["status"] == "ask"
            asks_made += predicted_ask
            asks_expected += expected_ask
            asks_tp += predicted_ask and expected_ask and plan.ask.field == exp.get("ask_field", plan.ask.field)
            if exp["status"] == "refuse":
                refusals_expected += 1
                refusals_right += plan.status == "refuse" and not t["errs"]
        by_cat.setdefault(case["category"], []).append(case_ok)
        if case["expected"].get("status") in COMMIT:
            questions_per_booking.append(sum(1 for t in turns if t["kind"] == "resolve" and t["plan"].status == "ask"))
        if not case_ok:
            failures.append((case, turns))

    # memo = the same request again in this process: no network, no cost, as in production.
    calls = [c for c in client.calls[n_calls_before:] if c.source != "memo"] if client else []
    timings: list[float] = []
    for _ in range(latency_repeats):
        for case in cases:
            run_case(index, case, hooks, timings)

    all_turns = sum(1 for c in cases for t in c["turns"] if "update" in t)
    return {
        "cases": len(cases), "evaluated": evaluated, "correct": correct, "resolve_turns": resolve_turns,
        "wrong_commits": wrong_commits, "commits": commits,
        "asks_made": asks_made, "asks_expected": asks_expected, "asks_tp": asks_tp,
        "refusals_expected": refusals_expected, "refusals_right": refusals_right,
        "qpb": statistics.mean(questions_per_booking) if questions_per_booking else 0.0,
        "bookings": len(questions_per_booking),
        "timings": timings, "say_tok": say_tok, "summary_tok": summary_tok, "result_tok": result_tok,
        "direct_tok": direct_tok, "by_cat": by_cat, "failures": failures,
        "jev_turns": jev_turns, "all_turns": all_turns, "jev_calls": calls,
    }


def _jev_rows(m: dict) -> list[tuple[str, str]]:
    calls = m["jev_calls"]
    if not calls:
        return [("JEV requests; turns where a JEV hook fired", f"0; {m['jev_turns']} of {m['all_turns']} turns")]
    live = [c.latency_ms for c in calls if c.source == "live"]
    recorded = [c.latency_ms for c in calls if c.source == "cache"]
    failed = sum(1 for c in calls if c.source == "failed")
    in_tok = [c.input_tokens for c in calls if c.source != "failed"]
    rows = [
        ("JEV requests (rate per resolve turn)", f"{len(calls)} ({len(calls) / max(m['all_turns'], 1):.1%}); "
                                                f"live {len(live)}, disk cache {len(recorded)}, failed {failed}"),
    ]
    if live:
        rows.append(("JEV latency p50 / p95 / max (live)", f"{pct(live, 50):.0f} / {pct(live, 95):.0f} / {max(live):.0f} ms"))
        rows.append(("  ... over the 1.2 s live budget", f"{sum(1 for x in live if x > 1200)} of {len(live)}"))
    if recorded:
        rows.append(("JEV latency p50 / p95 / max (recorded)", f"{pct(recorded, 50):.0f} / {pct(recorded, 95):.0f} / "
                                                              f"{max(recorded):.0f} ms  (as measured when cached)"))
    if in_tok:
        rows.append(("JEV input tokens total / per request", f"{sum(in_tok)} / {statistics.mean(in_tok):.0f}"))
        rows.append(("JEV cost if every request were live", f"${sum(in_tok) * USD_PER_INPUT_TOKEN:.5f}"))
    return rows


def metric_rows(m: dict) -> list[tuple[str, str]]:
    rows = [
        ("cases / evaluated turns", f"{m['cases']} / {m['evaluated']}"),
        ("WRONG-COMMIT rate per commit (headline)", wrong_commit(m) + f"  (over {m['resolve_turns']} resolve turns)"),
        ("top-1 (turn fully correct)", f"{m['correct']}/{m['evaluated']} = {m['correct'] / max(m['evaluated'], 1):.1%}"),
        ("ask precision (field-level)", f"{m['asks_tp']}/{m['asks_made']} = {m['asks_tp'] / max(m['asks_made'], 1):.1%}"),
        ("ask recall (field-level)", f"{m['asks_tp']}/{m['asks_expected']} = {m['asks_tp'] / max(m['asks_expected'], 1):.1%}"),
        ("mean questions per booking", f"{m['qpb']:.2f} over {m['bookings']} bookings"),
        ("refusal correctness", f"{m['refusals_right']}/{m['refusals_expected']} = "
                                f"{m['refusals_right'] / max(m['refusals_expected'], 1):.1%}"),
        ("resolver latency p50 / p95 (no network)", f"{pct(m['timings'], 50):.2f} ms / {pct(m['timings'], 95):.2f} ms"
                                                    f"  (n={len(m['timings'])})"),
        ("Plan.say tokens  mean / p95 / max", f"{statistics.mean(m['say_tok']):.0f} / {pct(m['say_tok'], 95)} / {max(m['say_tok'])}"),
        ("Plan.summary tokens mean / p95 / max", f"{statistics.mean(m['summary_tok']):.0f} / {pct(m['summary_tok'], 95)} / "
                                                 f"{max(m['summary_tok'])}"),
        ("tool result tokens mean / p95 / max", f"{statistics.mean(m['result_tok']):.0f} / {pct(m['result_tok'], 95)} / "
                                                f"{max(m['result_tok'])}"),
        ("  ... speak-direct (no say) mean/p95/max", f"{statistics.mean(m['direct_tok']):.0f} / {pct(m['direct_tok'], 95)} / "
                                                     f"{max(m['direct_tok'])}"),
    ]
    return rows + _jev_rows(m)


def print_table(title: str, m: dict) -> None:
    rows = metric_rows(m)
    width = max(len(k) for k, _ in rows)
    print(f"\n=== {title} ===")
    print("-" * (width + 60))
    for k, v in rows:
        print(f"{k:<{width}}  {v}")
    print("-" * (width + 60))
    print("by category (cases fully correct):")
    for cat, oks in sorted(m["by_cat"].items()):
        print(f"  {cat:<22} {sum(oks)}/{len(oks)}")
    if m["failures"]:
        print(f"MISSES ({len(m['failures'])}):")
        for case, turns in m["failures"]:
            for i, t in enumerate(turns):
                if t["errs"]:
                    said = t["plan"].say if t["kind"] == "resolve" else t["facts"]
                    print(f"  {case['id']} turn {i + 1}: {'; '.join(t['errs'])}\n      said: {said}")
                    for n in (t["plan"].notes if t["kind"] == "resolve" else ()):
                        if "disambiguator" in n or "chooser" in n:
                            print(f"      {n}")


def wrong_commit(m: dict) -> str:
    """Wrong commits over commits made: a turn that asks or refuses cannot commit wrongly, so
    dividing by all turns would flatter a resolver that commits rarely."""
    return f"{m['wrong_commits']}/{m['commits']} = {m['wrong_commits'] / max(m['commits'], 1):.1%}"


def print_headline(results: dict) -> None:
    """Per set, with the held-out sets (never used to write rules or tune thresholds) on their own."""
    groups = [("main (rules written against)", ("main",)), ("tune (thresholds chosen on)", ("tune",)),
              ("HELD-OUT heldout", ("heldout",)), ("HELD-OUT heldout2", ("heldout2",)),
              ("HELD-OUT combined", ("heldout", "heldout2")), ("national (catalog-pinned)", ("national",))]
    with_jev = any(on for _, on in results.values())

    def merged(names, which):
        ms = [results[n][which] for n in names if n in results and results[n][which]]
        if not ms:
            return None
        keys = ("wrong_commits", "commits", "correct", "evaluated")
        return {k: sum(m[k] for m in ms) for k in keys}

    print("\n=== HEADLINE: wrong commits per commit; top-1 per evaluated turn ===")
    print(f"{'set':<30}{'JEV off':<36}" + ("JEV on" if with_jev else ""))
    for label, names in groups:
        cols = []
        for which in (0, 1) if with_jev else (0,):
            m = merged(names, which)
            cols.append("-" if m is None else f"{wrong_commit(m)}; top-1 {m['correct']}/{m['evaluated']}")
        if cols[0] != "-":
            print(f"{label:<30}" + "".join(f"{c:<36}" for c in cols))


def print_comparison(name: str, off: dict, on: dict) -> None:
    def row(m):
        live = [c.latency_ms for c in m["jev_calls"] if c.source == "live"]
        tok = sum(c.input_tokens for c in m["jev_calls"] if c.source != "failed")
        return [wrong_commit(m),
                f"{m['correct']}/{m['evaluated']} ({m['correct'] / max(m['evaluated'], 1):.1%})",
                f"{m['qpb']:.2f}",
                f"{len(m['jev_calls'])} ({len(m['jev_calls']) / max(m['all_turns'], 1):.0%})",
                f"{pct(live, 50):.0f}/{pct(live, 95):.0f}/{max(live):.0f} ms" if live else "-",
                f"${tok * USD_PER_INPUT_TOKEN:.5f}"]
    labels = ["wrong-commit (per commit)", "top-1", "questions per booking", "JEV requests (rate)",
              "JEV latency p50/p95/max (live)", "JEV $ (input tokens x $0.04/M)"]
    a, b = row(off), row(on)
    print(f"\n--- {name}: JEV off vs on ---")
    print(f"{'metric':<32}{'off':<26}{'on':<26}")
    for label, x, y in zip(labels, a, b):
        print(f"{label:<32}{x:<26}{y:<26}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="all", choices=(*SETS, "all", "national"))
    ap.add_argument("--catalog", type=Path, help="catalog.json (default: SF, or national for --set national)")
    ap.add_argument("--jev", default="off", choices=("off", "on"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    if args.live and args.jev != "on":
        ap.error("--live needs --jev on")

    catalog = args.catalog or (NATIONAL_CATALOG if args.set == "national" else SF_CATALOG)
    if args.set == "national":
        actual = hashlib.sha256(catalog.read_bytes()).hexdigest()
        pinned = {c.get("catalog_sha256") for c in load_set("national")}
        if pinned != {actual}:
            ap.error(f"{catalog} has sha256 {actual}; cases_national.jsonl is pinned to {sorted(map(str, pinned))}")
    index = CatalogIndex.load(catalog)
    names = SETS if args.set == "all" else (args.set,)
    # Offline settings: patient timeout and one connect retry so the cache gets filled. The live
    # call path uses JevClient defaults (1.2 s per turn, no retry); see "over the live budget".
    client = JevClient.from_env(mode="live" if args.live else "cache", cache_path=CACHE, timeout_s=2.5, retries=1,
                                turn_budget_s=None) if args.jev == "on" else None
    hooks = make_hooks(index, client)
    print(f"Resolver eval (o200k_base tokenizer); JEV {args.jev}"
          + (" (LIVE network)" if args.live else " (disk cache only)" if client else ""))

    results = {}
    try:
        for name in names:
            cases = load_set(name)
            off = evaluate(index, cases, {}, None)
            on = evaluate(index, cases, hooks, client) if client else None
            results[name] = (off, on)
            print_table(f"{name}: JEV off", off)
            if on:
                print_table(f"{name}: JEV on", on)
            if args.verbose:
                for case in cases:
                    for i, t in enumerate(run_case(index, case, hooks)):
                        said = t["plan"].say if t["kind"] == "resolve" else t["facts"]
                        print(f"  {case['id']} t{i + 1}: {said}")
    finally:
        if client:
            client.save()
            client.close()

    if client:
        for name, (off, on) in results.items():
            print_comparison(name, off, on)
        live = [c for c in client.calls if c.source == "live"]
        if live:
            spent = sum(c.input_tokens for c in live)
            print(f"\nLIVE this run: {len(live)} requests, {spent} input tokens, ${spent * USD_PER_INPUT_TOKEN:.5f}")
    print_headline(results)


if __name__ == "__main__":
    main()
