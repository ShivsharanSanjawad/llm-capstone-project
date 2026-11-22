"""Run the evaluations and write the report.

  uv run python -m evals.run_evals                 # fast checks that need no LLM
  uv run python -m evals.run_evals --live          # also run the real model on the gold meetings (needs GROQ_API_KEY)
  uv run python -m evals.run_evals --live --cases circular_deadlock,small_talk
  uv run python -m evals.run_evals --live --mlflow # also log the run to MLflow / DagsHub (see tracking.py for the variables)

Outputs go to evals/results/: latest.json, REPORT.md, report.html.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("MONGODB_URL", "")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import deterministic, live, report
import tracking

RESULTS = Path(__file__).with_name("results")


def mlflow_payload(results):
    """Flatten the results into MLflow params, metrics and tags."""
    params = {"dataset": results["dataset"]["name"], "dataset_sha": results["dataset"]["sha"], "cases": results["dataset"]["cases"],
              "live": bool(results.get("live"))}
    metrics = {}
    for s in results["deterministic"]:
        key = s["name"].lower().replace(" ", "_").replace("-", "_")
        metrics[f"det.{key}.accuracy"] = s["accuracy"]
        metrics[f"det.{key}.passed"] = s["passed"]
        metrics[f"det.{key}.total"] = s["total"]
    metrics["det.checks_passed"] = sum(s["passed"] for s in results["deterministic"])
    metrics["det.checks_total"] = sum(s["total"] for s in results["deterministic"])
    tags = {"kind": "evaluation"}
    if results.get("live"):
        s = results["live"]["summary"]
        params.update(model=results["live"]["model"], provider=results["live"]["provider"])
        for name, value in (("task_precision", s["task_extraction"]["precision"]), ("task_recall", s["task_extraction"]["recall"]),
                            ("task_f1", s["task_extraction"]["f1"]), ("hallucination_rate", s["hallucination_rate"]),
                            ("owner_accuracy", s["owner_accuracy"]), ("deadline_accuracy", s["deadline_exact_date_accuracy"]),
                            ("invented_deadlines", s["invented_deadlines"]), ("dependency_precision", s["dependency_edges"]["precision"]),
                            ("dependency_recall", s["dependency_edges"]["recall"]), ("dependency_f1", s["dependency_edges"]["f1"]),
                            ("cycle_recall", s["cycle_detection"]["recall"]), ("cycle_precision", s["cycle_detection"]["precision"]),
                            ("external_blocker_recall", s["external_blocker_recall"]), ("evidence_grounded_rate", s.get("evidence_grounded_rate")),
                            ("latency_p50_s", s["latency_p50_s"]), ("tokens_per_meeting", s["tokens_per_meeting"]),
                            ("cost_per_meeting_usd", s["cost_per_meeting_usd"]), ("retries", s["retries"]), ("failed_cases", s["failed_cases"])):
            metrics[f"live.{name}"] = value
        for r in results["live"]["cases"]:
            for name in ("seconds", "input_tokens", "output_tokens", "task_tp", "task_fp", "task_fn", "owner_ok", "deadline_ok", "edge_tp", "edge_fn"):
                metrics[f"case.{r['case']}.{name}"] = r.get(name)
            tags[f"case.{r['case']}.problems"] = r["error"] or "; ".join(r["misses"]) or "none"
    return params, metrics, tags


async def main_async(args) -> dict:
    dataset = live.load_dataset()
    raw = json.dumps(dataset, sort_keys=True).encode()
    results = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "dataset": {"name": dataset["name"], "sha": hashlib.sha256(raw).hexdigest()[:12], "cases": len(dataset["cases"])},
        "deterministic": deterministic.run_all(),
        "live": None,
    }
    if args.live:
        only = [c.strip() for c in args.cases.split(",")] if args.cases else None
        results["live"] = await live.run_live(dataset, only, args.pause)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    (RESULTS / "REPORT.md").write_text(report.markdown(results), encoding="utf-8")
    (RESULTS / "report.html").write_text(report.html_page(results), encoding="utf-8")
    if args.mlflow:
        if not tracking.enabled():
            print("MLflow is not configured (set MLFLOW_TRACKING_URI and credentials); skipping upload.")
        else:
            params, metrics, tags = mlflow_payload(results)
            run_id = await tracking.send_run(f"eval {results['generated_at']}", params, metrics, tags,
                                             experiment=os.getenv("MLFLOW_EVAL_EXPERIMENT", "meeting-intelligence-evals"))
            print(f"MLflow run: {run_id}" if run_id else "MLflow upload failed (see the warning above); results are still saved locally.")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="run the real model on the gold meetings")
    parser.add_argument("--cases", default="", help="comma-separated case ids (with --live)")
    parser.add_argument("--pause", type=float, default=0, help="seconds to wait between live cases (helps on free-tier rate limits)")
    parser.add_argument("--mlflow", action="store_true", help="log the run to MLflow (needs MLFLOW_TRACKING_URI and credentials)")
    args = parser.parse_args()
    results = asyncio.run(main_async(args))
    for a, b, c in report.headline(results):
        print(f"{a:32} {b:>8}   {c}")
    print(f"\nReport: {RESULTS / 'REPORT.md'}\nPage:   {RESULTS / 'report.html'}")


if __name__ == "__main__":
    main()
