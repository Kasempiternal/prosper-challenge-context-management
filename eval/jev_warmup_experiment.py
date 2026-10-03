"""Does JevClient.warm_up() remove JEV's cold-start penalty? Live network, costs ~$0.0001/request.

Each trial opens a fresh client (new TCP+TLS connection) and sends requests with a phrase unique
to that trial, so no answer can come from any server-side response cache.
  cold   : real request first (no warm-up), then 2 more real requests
  warm   : warm_up(full 74-type criteria), then 3 real requests
  small  : a 2-option request first (pays connection + model, not the big criteria), then 3 real
Trials are separated by --gap minutes so the server can go cold again.

Usage: backend/.venv/Scripts/python eval/jev_warmup_experiment.py --plan cold,warm,small --gap 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from scheduling.catalog_index import CatalogIndex  # noqa: E402
from scheduling.jev import JevClient, offered_type_criteria  # noqa: E402

PHRASES = ("my yearly exam", "something for my back pain", "lung test")


def trial(kind: str, criteria: dict[str, str]) -> dict:
    client = JevClient.from_env(mode="live", timeout_s=30.0)
    tag = uuid.uuid4().hex[:6]
    out = {"kind": kind, "at": datetime.now().isoformat(timespec="seconds"), "pre_ms": None, "real_ms": []}
    if kind == "warm":
        call = client.warm_up_on(criteria)
        out["pre_ms"] = client.calls[-1].latency_ms if client.calls else None
        out["pre_ok"] = call is not None
    elif kind == "small":
        client.choice(f"[{tag}] A caller said: I want a flu shot", "Which service?",
                      {"a": "Flu Shot", "b": "Dental Cleaning"})
        out["pre_ms"] = client.calls[-1].latency_ms
    for phrase in PHRASES:
        client.choice(f"[{tag}] A patient calling a multi-specialty clinic said they want: '{phrase}'",
                      "Which appointment type is the caller asking for?", criteria)
        out["real_ms"].append(client.calls[-1].latency_ms)
    out["tokens"] = sum(c.input_tokens for c in client.calls)
    client.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="cold,warm,small")
    ap.add_argument("--gap", type=float, default=10.0, help="minutes between trials")
    ap.add_argument("--delay", type=float, default=0.0, help="minutes of idle before the first trial")
    args = ap.parse_args()
    time.sleep(args.delay * 60)
    criteria = offered_type_criteria(CatalogIndex.load(ROOT / "backend" / "data" / "catalog.json"))
    kinds = args.plan.split(",")
    for i, kind in enumerate(kinds):
        if i:
            time.sleep(args.gap * 60)
        print(json.dumps(trial(kind, criteria)), flush=True)


if __name__ == "__main__":
    main()
