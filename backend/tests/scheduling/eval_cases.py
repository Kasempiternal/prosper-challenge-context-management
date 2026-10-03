"""The eval case files tests may read. The blind held-out sets (heldout3, national3) are scored
once, by eval/run_resolver_eval.py, and never feed a test."""

from pathlib import Path

EVAL = Path(__file__).resolve().parents[3] / "eval"
BLIND = {"cases_heldout3.jsonl", "cases_national3.jsonl"}


def dev_case_files() -> list[Path]:
    return sorted(p for p in EVAL.glob("cases*.jsonl") if p.name not in BLIND)
