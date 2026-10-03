"""The eval case files tests may read: the dev sets of eval/sets.py. The blind held-out sets are
scored once, by eval/run_resolver_eval.py, and never feed a test."""

import importlib.util
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[3] / "eval"
_spec = importlib.util.spec_from_file_location("eval_sets", EVAL / "sets.py")
eval_sets = sys.modules["eval_sets"] = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_sets)


def dev_case_files() -> list[Path]:
    return sorted({EVAL / s.file for s in eval_sets.SETS.values() if not s.blind})
