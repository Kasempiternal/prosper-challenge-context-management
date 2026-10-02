"""Format check for eval case files. Runs no resolver and scores nothing.

  python eval/validate_case_format.py heldout3 national3

run_resolver_eval.py is read with `ast`, never imported: the set names (SETS, NATIONAL_SETS), the file
load_set maps each name to, and the expectation keys check_plan reads. For each case it checks that
keys are known, every type/provider/location id exists in the set's catalog, and that national cases
pin catalog_sha256 to the catalog (catalog.meta.json and the file's actual bytes).
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "eval"
RUNNER = EVAL / "run_resolver_eval.py"
SF = ROOT / "backend" / "data" / "catalog.json"
NATIONAL = ROOT / "backend" / "data" / "national" / "catalog.json"
NATIONAL_META = ROOT / "backend" / "data" / "national" / "catalog.meta.json"
TOP_KEYS = {"id", "category", "kind", "catalog_sha256", "patient", "turns", "expected", "phrasing_source"}
STATUSES = {"offer", "ask", "refuse", "confirm"}


def runner_facts() -> tuple[tuple, tuple, dict[str, str], set[str]]:
    src = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    consts, files, keys = {}, {}, set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("SETS", "NATIONAL_SETS"):
            consts[node.targets[0].id] = ast.literal_eval(node.value)
        if isinstance(node, ast.FunctionDef) and node.name == "load_set":
            d = next(n for n in ast.walk(node) if isinstance(n, ast.Dict))
            files = {k.value: v.right.value for k, v in zip(d.keys, d.values)}
            default = next(n for n in ast.walk(node) if isinstance(n, ast.Call)
                           and getattr(n.func, "attr", "") == "get").args[1].right.value
            files["main"] = files["heldout"] = default
        if isinstance(node, ast.FunctionDef) and node.name == "check_plan":
            body = ast.get_source_segment(src, node)
            keys |= set(re.findall(r'exp\["(\w+)"\]', body)) | set(re.findall(r'"(\w+)" in exp\b', body)) \
                | set(re.findall(r'exp\.get\("(\w+)"', body))
    return consts["SETS"], consts["NATIONAL_SETS"], files, keys


def known_update_keys(skip: set[str]) -> set[str]:
    """Update keys used by the committed case files this run is not validating."""
    out = set()
    for path in EVAL.glob("cases*.jsonl"):
        if path.name in skip:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                for turn in json.loads(line)["turns"]:
                    out |= set(turn.get("update", ()))
    return out


def check_expect(exp: dict, keys: set[str], cat: dict, where: str) -> list[str]:
    errs = [f"{where}: unknown expectation key {k!r}" for k in exp if k not in keys]
    if exp.get("status") not in STATUSES:
        errs.append(f"{where}: status {exp.get('status')!r}")
    ids = {"type": cat["types"], "provider": cat["providers"], "location": cat["locations"]}
    refs = [("type", t) for t in [exp.get("type_id")] + exp.get("types_any", []) if t] \
        + [("provider", p) for p in exp.get("provider_ids", [])] \
        + [("location", x) for x in exp.get("location_ids", []) + exp.get("location_ids_subset", [])]
    field = {"service": "type", "provider": "provider", "location": "location"}.get(exp.get("ask_field"))
    if exp.get("ask_options") is not None:
        if field is None:
            errs.append(f"{where}: ask_options with ask_field {exp.get('ask_field')!r}")
        else:
            refs += [(field, o) for o in exp["ask_options"]]
    for alt in exp.get("alternatives", []):
        refs += list(zip(("type", "provider", "location"), alt))
    errs += [f"{where}: {kind} id {i!r} not in catalog" for kind, i in refs if i not in ids[kind]]
    if ("max_miles" in exp) != ("anchor" in exp):
        errs.append(f"{where}: max_miles and anchor must come together")
    return errs


def load_catalog(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {"types": {t["id"] for t in raw["appointment_types"]}, "providers": {p["id"] for p in raw["providers"]},
            "locations": {x["id"] for x in raw["locations"]}}


def validate(name: str, sets, national_sets, files, keys, update_keys) -> list[str]:
    if name not in sets and name not in national_sets:
        return [f"{name}: not in SETS or NATIONAL_SETS of run_resolver_eval.py"]
    if name not in files:
        return [f"{name}: load_set has no file for it"]
    path = EVAL / files[name]
    if not path.exists():
        return [f"{name}: load_set maps to missing {path.name}"]
    national = name in national_sets
    cat = load_catalog(NATIONAL if national else SF)
    pinned = json.loads(NATIONAL_META.read_text(encoding="utf-8"))["sha256"] if national else None
    actual = hashlib.sha256(NATIONAL.read_bytes()).hexdigest() if national else None
    errs, seen = [], set()
    if national and pinned != actual:
        errs.append(f"{name}: national catalog file sha {actual} != catalog.meta.json {pinned}")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for n, line in enumerate(lines, 1):
        try:
            c = json.loads(line)
        except json.JSONDecodeError as e:
            errs.append(f"{path.name}:{n}: not JSON ({e})")
            continue
        where = f"{path.name}:{n} {c.get('id')}"
        errs += [f"{where}: unknown key {k!r}" for k in c if k not in TOP_KEYS]
        errs += [f"{where}: missing {k!r}" for k in ("id", "category", "patient", "turns", "expected") if k not in c]
        if c.get("id") in seen:
            errs.append(f"{where}: duplicate id")
        seen.add(c.get("id"))
        pat = c.get("patient", {})
        errs += [f"{where}: patient key {k!r}" for k in pat if k not in ("is_new", "has_referral")]
        errs += [f"{where}: patient {k} not bool" for k, v in pat.items() if not isinstance(v, bool)]
        turns = c.get("turns") or []
        if not turns:
            errs.append(f"{where}: no turns")
        for i, t in enumerate(turns):
            errs += [f"{where}: turn {i} key {k!r}" for k in t if k not in ("update", "expect")]
            upd = t.get("update", {})
            errs += [f"{where}: turn {i} update key {k!r}" for k in upd if k not in update_keys]
            errs += [f"{where}: turn {i} empty {k}" for k, v in upd.items() if not isinstance(v, str) or not v.strip()]
            if "expect" in t:
                if i == len(turns) - 1:
                    errs.append(f"{where}: last turn carries expect; use expected")
                errs += check_expect(t["expect"], keys, cat, f"{where} turn {i}")
        errs += check_expect(c.get("expected", {}), keys, cat, where)
        if national and c.get("catalog_sha256") != pinned:
            errs.append(f"{where}: catalog_sha256 {c.get('catalog_sha256')!r} != pinned {pinned}")
    print(f"{name}: {path.name}, {len(lines)} cases, {'national' if national else 'SF'} catalog, "
          f"{len(errs)} problems")
    return errs


def main() -> None:
    names = sys.argv[1:]
    if not names:
        sys.exit("usage: validate_case_format.py SET [SET ...]")
    sets, national_sets, files, keys = runner_facts()
    print(f"check_plan expectation keys: {sorted(keys)}")
    update_keys = known_update_keys({files[n] for n in names if n in files})
    print(f"update keys seen in other case files: {sorted(update_keys)}")
    errs = [e for name in names for e in validate(name, sets, national_sets, files, keys, update_keys)]
    for e in errs:
        print("  " + e)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
