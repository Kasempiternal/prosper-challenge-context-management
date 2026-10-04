"""Input tokens per LLM request for the two shipped agents, from their real node configs. Offline.

Builds each node exactly as the call does (AgentBuilder._make_node): role message + task messages
with {{ summary }} and {{ today }} filled in + the tool schemas of the node (its tools and its edges).
Spoken history is left out (same for any approach). What grows on the schedule node is our tool
traffic: each update_request call and its result stay in the context, measured per exchange over the
eval cases. Counts use tiktoken o200k_base on compact JSON; OpenAI's own serialisation of tools adds a
little, so read them as +-5%.

    backend/.venv/Scripts/python eval/prompt_tokens.py
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "eval"))

from agent_builder import AgentBuilder  # noqa: E402
from naive_baseline_tokens import exchange_tokens  # noqa: E402
from run_resolver_eval import run_case  # noqa: E402
from scheduling.catalog_index import CatalogIndex  # noqa: E402

ENC = tiktoken.get_encoding("o200k_base")
AGENTS = (
    ("Clinic Scheduler (SF)", "clinic-scheduler.json", "eval/cases.jsonl", "backend/data/catalog.json"),
    ("National Scheduler", "national-scheduler.json", "eval/cases_national.jsonl", "backend/data/national/catalog.json"),
)


def tok(obj) -> int:
    text = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return len(ENC.encode(text))


def exchanges_and_summaries(catalog: str, cases: str) -> tuple[list[int], list[int]]:
    index = CatalogIndex.load(ROOT / catalog)
    exchanges, summaries = [], []
    for line in (ROOT / cases).read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        for turn, t in zip(case["turns"], run_case(index, case, {})):
            if t["kind"] == "resolve":
                exchanges.append(exchange_tokens(turn["update"], t["plan"]))
                summaries.append(tok(t["plan"].summary))
    return exchanges, summaries


def main() -> None:
    for label, agent_file, cases, catalog in AGENTS:
        builder = AgentBuilder.from_json(ROOT / "backend" / "agents" / agent_file)
        exchanges, summaries = exchanges_and_summaries(catalog, cases)
        mean_x, max_x, max_s = round(statistics.mean(exchanges)), max(exchanges), max(summaries)
        today = "Wednesday, October 7, 2026"
        naive_catalog = tok(json.loads((ROOT / catalog).read_text(encoding="utf-8")))
        print(f"\n{label}   ({len(exchanges)} eval turns; exchange mean {mean_x}, max {max_x}; summary max {max_s})")
        print(f"{'node':<14}{'prompt':>8}{'tools':>8}{'fixed':>8}")
        worst = 0
        for node in builder.config.nodes:
            cfg = builder._make_node(node)
            prompt_text = "\n".join([cfg["role_message"] or "", *(m["content"] for m in cfg["task_messages"])])
            prompt_text = prompt_text.replace("{{ summary }}", "x" * 0).replace("{{ today }}", today)
            tools = [{"type": "function", "function": {
                "name": f.name, "description": f.description,
                "parameters": {"type": "object", "properties": f.properties, "required": f.required}}}
                for f in cfg["functions"]]
            p, t = tok(prompt_text), tok(tools)
            summary = max_s if node.name == "schedule" else 0
            fixed = p + t + summary
            worst = max(worst, fixed)
            print(f"{node.name:<14}{p:>8,}{t:>8,}{fixed:>8,}" + ("   (+ summary, max)" if summary else ""))
            if node.name == "schedule":
                sched = fixed
        print(f"schedule node, request N of a call (fixed + N-1 earlier exchanges, mean | max):")
        for n in (1, 5, 10, 15):
            print(f"  request {n:>2}: {sched + (n - 1) * mean_x:>6,} | {sched + (n - 1) * max_x:>6,}")
        print(f"naive (same prompt + whole catalog): {sched - max_s + naive_catalog:>9,}  (catalog alone {naive_catalog:,})")


if __name__ == "__main__":
    main()
