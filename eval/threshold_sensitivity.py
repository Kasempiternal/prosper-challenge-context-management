"""Which dev outcomes flip when a JEV threshold moves by -0.05 / +0.05, the others at their shipped
values. Offline, from eval/.jev_cache.json; every set is a dev set (the round 3 held-out sets are
not read). Usage: backend/.venv/Scripts/python eval/threshold_sensitivity.py
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_resolver_eval as E  # noqa: E402

import scheduling.decision as D  # noqa: E402
from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.decision import CheckGate, Gate, Verdict  # noqa: E402
from scheduling.jev import JevProviderChooser, JevSiteChooser, JevTypeDisambiguator  # noqa: E402
from sets import DEV  # noqa: E402

STEP = 0.05


class MarginGate(CheckGate):
    """The shipped gate, with a margin on its act rule: a confident choice stands only if the check
    puts it more than `margin` ahead of the rival (shipped: 0)."""

    def __init__(self, margin: float):
        super().__init__()
        object.__setattr__(self, "margin", margin)

    def decide(self, first, rival, check):
        verdict = super().decide(first, rival, check)
        if first.act and check is not None and check.either < self.twins:
            if check.chosen > check.rival + self.margin:
                return Verdict(act=first.act, top=verdict.top, called=True)
            return Verdict(ask=(first.act, rival), top=verdict.top, called=True)
        return verdict


def outcomes(check_gate: CheckGate, gender_sure: float, indexes: dict) -> tuple[dict, int]:
    D.GENDER_SURE = gender_sure
    out, failed = {}, 0
    for name in DEV:
        index = indexes[E.catalog_of(name)]
        client = E.make_client("jev")
        hooks = {"disambiguator": JevTypeDisambiguator(index, client, Gate(), check_gate),
                 "chooser": JevProviderChooser(index, client), "site_chooser": JevSiteChooser(index, client)}
        try:
            for case in E.load_set(name):
                for i, t in enumerate(E.run_case(index, case, hooks)):
                    if t["kind"] == "resolve" and t["errs"] is not None:
                        plan = t["plan"]
                        picks = list(plan.offers) + ([plan.confirm] if plan.confirm else [])
                        out[(name, case["id"], i + 1)] = (
                            plan.status, plan.ask and (plan.ask.field, plan.ask.options),
                            tuple(sorted({(o.type_id, o.provider_id) for o in picks})),
                            not t["errs"], E.is_wrong_commit(plan, t["errs"]))
            failed += sum(1 for c in client.calls if c.source == "failed")
        finally:
            client.close()
    return out, failed


def main() -> None:
    indexes = {c: CatalogIndex.load(c) for c in (E.SF_CATALOG, E.NATIONAL_CATALOG)}
    shipped_gender = D.GENDER_SURE
    base, failed = outcomes(CheckGate(), shipped_gender, indexes)
    print(f"shipped: top-1 {sum(v[3] for v in base.values())}/{len(base)}, wrong commits "
          f"{sum(v[4] for v in base.values())}, failed requests {failed}")
    variants = []
    for f in fields(CheckGate):
        for d in (-STEP, STEP):
            value = round(getattr(CheckGate(), f.name) + d, 2)
            variants.append((f"{f.name} {value:.2f}", CheckGate(**{f.name: value}), shipped_gender))
    variants += [(f"act margin {d:+.2f}", MarginGate(d), shipped_gender) for d in (-STEP, STEP)]
    variants += [(f"gender {round(shipped_gender + d, 2):.2f}", CheckGate(), round(shipped_gender + d, 2))
                 for d in (-STEP, STEP)]
    for label, gate, gender in variants:
        got, failed = outcomes(gate, gender, indexes)
        flips = [(k, base[k], got[k]) for k in base if base[k] != got[k]]
        print(f"\n{label}: {len(flips)} flips; top-1 {sum(v[3] for v in got.values()) - sum(v[3] for v in base.values()):+d};"
              f" wrong commits {sum(v[4] for v in got.values()) - sum(v[4] for v in base.values()):+d};"
              f" failed requests {failed}")
        for (name, cid, turn), a, b in flips:
            print(f"   {name} {cid} t{turn}: {a[0]} {a[1] or a[2]} ok={a[3]} -> {b[0]} {b[1] or b[2]} ok={b[3]} "
                  f"wrong={b[4]}")
    D.GENDER_SURE = shipped_gender


if __name__ == "__main__":
    main()
