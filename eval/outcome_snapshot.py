"""Every dev turn's outcome, offline, as one JSON file; and the turns that differ between two such
files. Also counts model requests and how many follow one another within a turn.

  backend/.venv/Scripts/python eval/outcome_snapshot.py --chooser jev|none|openai|embed --out FILE [--sets a,b]
  backend/.venv/Scripts/python eval/outcome_snapshot.py --diff BEFORE AFTER

Requests are the eval's count (a repeat of the same request in the run is free and not counted).
Sequential depth: a request costs one unit of time; one sent early (prefetch) runs from when it
was sent, so the turn's depth is its critical path in requests. The JEV and OpenAI choosers read
their committed caches; a request missing from the cache counts as failed, as in the eval.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_resolver_eval as E  # noqa: E402

from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.model_client import cache_key  # noqa: E402
from sets import DEV  # noqa: E402


class _Depth:
    """Wraps a cached client: a virtual clock per turn, one unit per request."""

    def __init__(self, client):
        self.now, self.inflight = 0, {}
        prefetch, fetch = client.prefetch, client._fetch

        def early(body):
            key = cache_key(body)
            if key not in client._memo:
                self.inflight.setdefault(key, self.now + 1)
            prefetch(body)

        def timed(body, purpose, read):
            key = cache_key(body)
            if key not in client._memo:
                self.now = max(self.now, self.inflight.pop(key, self.now + 1))
            return fetch(body, purpose, read)

        client.prefetch, client._fetch = early, timed

    def reset(self) -> None:
        self.now, self.inflight = 0, {}


def snapshot(chooser: str, names: tuple[str, ...]) -> dict:
    indexes: dict = {}
    real_resolve = E.resolve
    out = {}
    for name in names:
        catalog = E.catalog_of(name)
        index = indexes.setdefault(catalog, CatalogIndex.load(catalog))
        client = E.make_client(chooser) if chooser != "none" else None
        depth = _Depth(client) if client is not None and hasattr(client, "_fetch") else None
        hooks = E.make_hooks(index, client)
        depths: list[int] = []

        def resolve(*a, **kw):
            if depth:
                depth.reset()
            plan = real_resolve(*a, **kw)
            depths.append(depth.now if depth else 0)
            return plan

        E.resolve = resolve
        try:
            for case in E.load_set(name):
                start = len(depths)
                turns = E.run_case(index, case, hooks, client=client)
                resolve_depths = iter(depths[start:])
                for i, t in enumerate(turns):
                    if t["kind"] != "resolve":
                        continue
                    d = next(resolve_depths)
                    plan = t["plan"]
                    picks = list(plan.offers) + ([plan.confirm] if plan.confirm else [])
                    calls = [c for c in t["calls"] if c.source != "memo"]
                    out[f"{name} {case['id']} t{i + 1}"] = {
                        "set": name, "scored": t["errs"] is not None, "ok": not t["errs"],
                        "wrong": E.is_wrong_commit(plan, t["errs"] or []),
                        "status": plan.status, "ask": [plan.ask.field, list(plan.ask.options)] if plan.ask else None,
                        "picks": sorted({f"{o.type_id}/{o.provider_id}" for o in picks}),
                        "refusal": plan.refusal.code if plan.refusal else None,
                        "requests": len(calls), "failed": sum(c.source == "failed" for c in calls), "depth": d,
                        "say": plan.say}
        finally:
            E.resolve = real_resolve
            if client is not None:
                client.close()
    return out


def summary(snap: dict) -> None:
    sets = list(dict.fromkeys(v["set"] for v in snap.values()))
    print(f"{'set':<11}{'wrong/commits':>15}{'top-1':>11}{'requests':>10}{'/turn':>7}{'depth>=3':>10}{'failed':>8}")
    for s in sets:
        vs = [v for v in snap.values() if v["set"] == s]
        scored = [v for v in vs if v["scored"]]
        commits = sum(v["status"] in E.COMMIT for v in scored)
        req = sum(v["requests"] for v in vs)
        print(f"{s:<11}{sum(v['wrong'] for v in scored):>8}/{commits:<6}{sum(v['ok'] for v in scored):>5}/"
              f"{len(scored):<5}{req:>10}{req / len(vs):>7.2f}{sum(v['depth'] >= 3 for v in vs):>10}"
              f"{sum(v['failed'] for v in vs):>8}")


def diff(before: dict, after: dict) -> None:
    for key in before:
        a, b = before[key], after.get(key)
        if b is None:
            print(f"{key}: missing after")
            continue
        fields = ("ok", "status", "ask", "picks", "refusal")
        if any(a[f] != b[f] for f in fields) or a["say"] != b["say"]:
            mark = "WRONG " if b["wrong"] else ("+ " if b["ok"] and not a["ok"] else "- " if a["ok"] and not b["ok"] else "  ")
            print(f"{mark}{key}: {a['status']} {a['ask'] or a['picks'] or a['refusal']} ok={a['ok']} -> "
                  f"{b['status']} {b['ask'] or b['picks'] or b['refusal']} ok={b['ok']}\n      {b['say']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chooser", default="jev")
    ap.add_argument("--sets", default=",".join(DEV))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--diff", nargs=2, type=Path)
    args = ap.parse_args()
    if args.diff:
        before, after = (json.loads(p.read_text(encoding="utf-8")) for p in args.diff)
        diff(before, after)
        print("\nbefore:")
        summary(before)
        print("after:")
        summary(after)
        return
    snap = snapshot(args.chooser, tuple(args.sets.split(",")))
    if args.out:
        args.out.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    summary(snap)


if __name__ == "__main__":
    main()
