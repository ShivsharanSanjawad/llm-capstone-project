"""Live evaluation: run the real task pipeline (the real model) on the gold transcripts and score it. Needs an LLM key."""
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("MONGODB_URL", "")           # never touch a real database
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline_metrics as pm
import tracking
from deadlines import apply_deadlines
from graph_engine import analyze_task_graph
from priority import apply_priority
from evals import metrics

DATASET = Path(__file__).with_name("dataset.json")


def load_dataset(path: Path = DATASET) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def run_case(case: Dict[str, Any], main) -> Dict[str, Any]:
    """The same sequence as /api/meetings/process, minus the database."""
    day = date.fromisoformat(case["meeting_date"])
    trace = tracking.begin("eval_case")
    started = time.monotonic()
    error: Optional[str] = None
    tasks: List[Dict[str, Any]] = []
    graph: Dict[str, Any] = {}
    try:
        factual = await main.run_factual_extraction(case["transcript"], day.isoformat())
        factual_tasks = apply_deadlines([t.model_dump() for t in factual.tasks], day)
        enriched = await main.run_contextual_inference(case["transcript"], factual_tasks, day.isoformat())
        tasks = apply_deadlines([t.model_dump() for t in enriched.enriched_tasks], day)
        apply_priority(tasks, day)
        graph = analyze_task_graph(tasks, enriched.model_dump())
    except Exception as exc:
        error = f"{type(exc).__name__}: {getattr(exc, 'detail', exc)}"
    elapsed = time.monotonic() - started
    usage = dict(trace.metrics)
    trace.discard()
    row = metrics.score_case(case, tasks, graph)
    row.update(error=error, seconds=round(elapsed, 2),
               input_tokens=usage.get("llm.total_input_tokens", 0), output_tokens=usage.get("llm.total_output_tokens", 0),
               llm_calls=usage.get("llm.total_calls", 0), retries=usage.get("llm.total_retries", 0),
               cost_usd=round(sum(v for k, v in usage.items() if k.endswith("estimated_cost_usd")), 6),
               evidence=pm.dependency_stats(tasks, case["transcript"]),
               tasks=[{"id": t["task_id"], "description": t["description"], "owner": t.get("owner"),
                       "deadline": t.get("deadline"), "deadline_date": t.get("deadline_date"),
                       "priority": t.get("priority"), "priority_score": t.get("priority_score")} for t in tasks])
    return row


async def run_live(dataset: Dict[str, Any], only: Optional[List[str]] = None, pause: float = 0) -> Dict[str, Any]:
    import main                                                       # imported late: it reads the environment
    if not main.llm_service.client:
        raise SystemExit("No LLM provider configured. Set GROQ_API_KEY in task-backend/.env to run the live evaluation.")
    rows = []
    for case in dataset["cases"]:
        if only and case["id"] not in only:
            continue
        rows.append(await run_case(case, main))
        if pause:
            await asyncio.sleep(pause)
    ok = [r for r in rows if not r["error"]]
    seconds = sorted(r["seconds"] for r in ok)
    quotes = sum(r["evidence"].get("evidence_quotes", 0) for r in ok)
    grounded = sum(round(r["evidence"].get("evidence_grounded_rate", 0) * r["evidence"].get("evidence_quotes", 0)) for r in ok)
    summary = metrics.aggregate(ok)
    summary.update(
        evidence_grounded_rate=round(grounded / quotes, 3) if quotes else None, evidence_quotes=quotes,
        failed_cases=len(rows) - len(ok),
        latency_p50_s=round(statistics.median(seconds), 2) if seconds else None,
        latency_max_s=max(seconds, default=None),
        tokens_per_meeting=round(sum(r["input_tokens"] + r["output_tokens"] for r in ok) / len(ok)) if ok else None,
        cost_per_meeting_usd=round(sum(r["cost_usd"] for r in ok) / len(ok), 5) if ok else None,
        retries=sum(r["retries"] for r in ok))
    return {"model": main.llm_service.model, "provider": main.llm_service.provider, "summary": summary, "cases": rows}
