"""Verify the live-demo tool arguments offline. No audio, LLM extraction or network calls.

Run from the repo root: backend/.venv/Scripts/python backend/tools/check_live_script.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from scheduling.availability import MockAvailability
from scheduling.catalog_index import CatalogIndex
from scheduling.choosers import CHOOSERS
from scheduling.decision import Gate
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve


def main():
    cases = json.loads((ROOT / "docs/live-call-cases.json").read_text(encoding="utf-8"))
    indexes = {name: CatalogIndex.load(ROOT / path) for name, path in {
        "sf": "backend/data/catalog.json", "national": "backend/data/national/catalog.json"}.items()}
    checked = 0
    for case in cases:
        ix = indexes[case["catalog"]]
        for mode, expectations in case["expectations"].items():
            assert len(expectations) == len(case["updates"]), case["name"]
            client = CHOOSERS[mode].make_client(mode="cache", cache_path=ROOT / "eval/.jev_cache.json") if mode == "jev" else None
            hooks = CHOOSERS[mode].hooks_for(ix, client, Gate()) if client else {}
            req, av = Request(), MockAvailability(ix)
            try:
                for update, exp in zip(case["updates"], expectations):
                    plan = resolve(ix, merge(req, Update.from_args(update)), av, **hooks)
                    req = plan.req
                    context = (case["name"], mode, update, plan.say)
                    assert plan.status in exp["status"].split("|"), context
                    if "field" in exp:
                        assert plan.ask and plan.ask.field in exp["field"].split("|"), context
                    if "options" in exp:
                        assert set(plan.ask.options) == set(exp["options"]), context
                    picks = plan.offers or ((plan.confirm,) if plan.confirm else ())
                    for attr in ("type_id", "provider_id", "location_id"):
                        if attr in exp:
                            assert picks and {getattr(o, attr) for o in picks} <= set(exp[attr]), context
                    if "refuse_code" in exp:
                        assert plan.refusal and plan.refusal.code == exp["refuse_code"], context
                    checked += 1
                    print(f"PASS {case['name']} [{mode}]: {plan.say}")
            finally:
                if client:
                    client.close()
    print(f"Verified {len(cases)} scenarios, {checked} resolver turns. Network disabled.")


if __name__ == "__main__":
    main()
