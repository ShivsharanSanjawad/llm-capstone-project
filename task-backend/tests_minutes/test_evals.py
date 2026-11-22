"""Guards for the evaluation harness itself, and a regression gate on the checks that need no LLM."""
from evals import deterministic, metrics, report

CASE = {"id": "c", "gold": {"tasks": [
    {"id": "G1", "keywords": ["login"], "owner": "Rahul", "deadline_date": "2026-11-27"},
    {"id": "G2", "keywords": ["api", "keys"], "owner": "Kunal", "deadline_date": None}],
    "dependencies": [["G2", "G1"]], "has_cycle": False, "external_blockers": {"G1": ["approv"]}}}


def task(tid, description, owner=None, deadline_date=None, pre=(), inferred=()):
    return {"task_id": tid, "description": description, "owner": owner, "deadline_date": deadline_date,
            "explicit_prerequisites": list(pre), "inferred_prerequisites": list(inferred)}


def test_a_perfect_answer_scores_perfectly():
    tasks = [task("T1", "Finish the login page", "Rahul", "2026-11-27", pre=["T2", "Waiting for approval"]), task("T2", "Send the API keys", "Kunal")]
    row = metrics.score_case(CASE, tasks, {"is_feasible": True})
    assert (row["task_tp"], row["task_fp"], row["task_fn"]) == (2, 0, 0)
    assert (row["owner_ok"], row["owner_total"], row["deadline_ok"], row["deadline_total"]) == (2, 2, 1, 1)
    assert (row["edge_tp"], row["edge_fp"], row["edge_fn"]) == (1, 0, 0)
    assert row["blocker_found"] == 1 and row["misses"] == []
    agg = metrics.aggregate([row])
    assert agg["task_extraction"]["f1"] == 1.0 and agg["owner_accuracy"] == 1.0 and agg["hallucination_rate"] == 0.0


def test_mistakes_are_counted_and_explained():
    tasks = [task("T1", "Finish the login page", "Amit", "2026-12-01"), task("T9", "Water the plants", "Zed")]
    row = metrics.score_case(CASE, tasks, {"is_feasible": False})
    assert (row["task_tp"], row["task_fp"], row["task_fn"]) == (1, 1, 1)
    assert row["owner_ok"] == 0 and row["deadline_ok"] == 0
    assert row["edge_fn"] == 1 and row["cycle_predicted"] == 1 and row["cycle_gold"] == 0
    text = " ".join(row["misses"])
    assert "owner of G1" in text and "deadline of G1" in text and "missed dependency G2 -> G1" in text and "cycle invented" in text


def test_matching_is_one_to_one_and_ignores_unrelated_tasks():
    pairs = metrics.match_tasks([task("T1", "login page"), task("T2", "login screen too")], [CASE["gold"]["tasks"][0]])
    assert list(pairs.values()) == ["G1"] and len(pairs) == 1


def test_empty_meetings_and_rates():
    quiet = {"id": "q", "gold": {"tasks": [], "dependencies": [], "has_cycle": False}}
    clean = metrics.score_case(quiet, [], {"is_feasible": True})
    noisy = metrics.score_case(quiet, [task("T1", "invented")], {"is_feasible": True})
    assert metrics.aggregate([clean])["no_task_cases_clean"] == 1.0
    assert metrics.aggregate([noisy])["no_task_cases_clean"] == 0.0 and metrics.aggregate([noisy])["hallucination_rate"] == 1.0
    assert metrics.prf(0, 0, 0) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert metrics.prf(1, 1, 1)["f1"] == 0.5


def test_every_no_llm_check_passes():
    """A regression gate: if a change breaks deadline parsing, name filtering, the graph engine or the verifier, this fails."""
    for suite in deterministic.run_all():
        assert suite["failures"] == [], (suite["name"], suite["failures"])


def test_report_renders_with_and_without_the_live_section():
    results = {"generated_at": "now", "dataset": {"name": "d", "sha": "abc", "cases": 1}, "deterministic": deterministic.run_all(), "live": None}
    assert "Automated checks" in report.markdown(results) and "<html" in report.html_page(results)
    row = metrics.score_case(CASE, [task("T1", "login page", "Rahul", "2026-11-27")], {"is_feasible": True})
    row.update(error=None, seconds=1.0, input_tokens=1, output_tokens=1, llm_calls=2, retries=0, cost_usd=0.0, evidence={}, tasks=[])
    summary = metrics.aggregate([row])
    summary.update(evidence_grounded_rate=None, evidence_quotes=0, failed_cases=1, latency_p50_s=1.0, latency_max_s=1.0,
                   tokens_per_meeting=2, cost_per_meeting_usd=0.0, retries=0)
    results["live"] = {"model": "m", "provider": "p", "summary": summary, "cases": [row]}
    md, page = report.markdown(results), report.html_page(results)
    assert "Meetings processed" in md and "0/0" not in md and "1/2" in md and "Per meeting" in md and "Live model" in page
