"""The eval sets, as data: which case file each name reads, against which catalog, and whether it is
blind. Imported by the eval scripts and, by path, by backend/tests (which never read a blind set).

national: the case file is scored against the national catalog, and every case pins that
catalog's sha256 (load_set refuses a mismatch). in_all: part of `--set all`. blind: held out,
scored once; no test, tuning or format sweep reads it. heldout: cases.jsonl holds two sets, told
apart by category ("heldout" or not).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalSet:
    file: str
    national: bool = False
    in_all: bool = False
    blind: bool = False
    heldout: bool | None = None


SETS: dict[str, EvalSet] = {
    "main": EvalSet("cases.jsonl", in_all=True, heldout=False),
    "heldout": EvalSet("cases.jsonl", in_all=True, heldout=True),
    "heldout2": EvalSet("cases_heldout2.jsonl", in_all=True),
    "tune": EvalSet("cases_tune.jsonl", in_all=True),
    "heldout3": EvalSet("cases_heldout3.jsonl", blind=True),
    "national": EvalSet("cases_national.jsonl", national=True),
    "national2": EvalSet("cases_national2.jsonl", national=True),
    "street": EvalSet("cases_street.jsonl", national=True),
    "national3": EvalSet("cases_national3.jsonl", national=True, blind=True),
}
DEV = tuple(name for name, s in SETS.items() if not s.blind)
BLIND_FILES = frozenset(s.file for s in SETS.values() if s.blind)

# Every expectation key run_resolver_eval.check_plan reads (validate_case_format.py checks new files against it).
EXPECT_KEYS = frozenset({
    "status", "type_id", "provider_ids", "location_ids", "weekday", "part_of_day", "ask_field", "ask_options",
    "refuse_code", "alternatives", "types_any", "location_ids_subset", "max_miles", "anchor", "refuse_reason",
})
