"""Catalog queries for authoring the stress suite. Ground truth comes from here and from the booking
rules in docs/PHASE2_DESIGN.md (section "policy"), never from the resolver: this module imports
nothing from backend/scheduling.

  python eval/stress/catq.py sf|nat types  REGEX
  python eval/stress/catq.py sf|nat prov   REGEX
  python eval/stress/catq.py sf|nat loc    REGEX
  python eval/stress/catq.py sf|nat rows   TYPE_ID [new|old] [noref|ref]   # valid (provider, location) rows
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATHS = {"sf": ROOT / "backend" / "data" / "catalog.json", "nat": ROOT / "backend" / "data" / "national" / "catalog.json"}


@lru_cache(maxsize=None)
def cat(which: str) -> dict:
    raw = json.loads(PATHS[which].read_text(encoding="utf-8"))
    return {"raw": raw, "types": {t["id"]: t for t in raw["appointment_types"]},
            "provs": {p["id"]: p for p in raw["providers"]}, "locs": {x["id"]: x for x in raw["locations"]}}


def sha256(which: str) -> str:
    return hashlib.sha256(PATHS[which].read_bytes()).hexdigest()


def find(which: str, kind: str, pattern: str) -> list[dict]:
    c = cat(which)
    pool = {"types": c["types"], "prov": c["provs"], "loc": c["locs"]}[kind]
    rx = re.compile(pattern, re.I)
    return [v for v in pool.values() if rx.search(json.dumps(v))]


def rows(which: str, type_id: str, is_new: bool | None = None, has_referral: bool | None = None) -> list[tuple[str, str]]:
    """Bookable (provider_id, location_id) rows for a type after the six booking rules.
    Unknown patient facts (None) do not remove a row, as in policy.check."""
    c = cat(which)
    t = c["types"][type_id]
    out = []
    for p in c["provs"].values():
        if type_id not in p["appointment_type_ids"]:
            continue
        if is_new and (not t["new_patients_allowed"] or not p["accepting_new_patients"]):
            continue
        if t["requires_referral"] and has_referral is False:
            continue
        for lid in p["location_ids"]:
            cap = t.get("required_capability")
            if cap and cap not in c["locs"][lid]["capabilities"]:
                continue
            out.append((p["id"], lid))
    return out


def short(which: str, kind: str, d: dict) -> str:
    if kind == "types":
        return f"{d['id']} {d['name']} [{d['specialty']}] ref={d['requires_referral']} new={d['new_patients_allowed']} cap={d.get('required_capability')}"
    if kind == "prov":
        return (f"{d['id']} {d['name']} {d['title']} {d['specialty']} acc={d['accepting_new_patients']} {d['languages']} "
                f"locs={d['location_ids']} ntypes={len(d['appointment_type_ids'])}")
    return f"{d['id']} {d['name']} | {d['address']}, {d['city']} {d.get('state', '')} {d.get('zip', '')} {d.get('neighborhood', '')} caps={d['capabilities']}"


if __name__ == "__main__":
    which, kind, arg = sys.argv[1:4]
    if kind == "rows":
        flags = sys.argv[4:]
        r = rows(which, arg, True if "new" in flags else False if "old" in flags else None,
                 True if "ref" in flags else False if "noref" in flags else None)
        print(len(r), "rows;", len({p for p, _ in r}), "providers")
        for p, l in r:
            print(p, cat(which)["provs"][p]["name"], l, cat(which)["locs"][l]["name"])
    else:
        for d in find(which, kind, arg):
            print(short(which, kind, d))
