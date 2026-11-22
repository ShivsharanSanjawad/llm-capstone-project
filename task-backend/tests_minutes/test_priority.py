from datetime import date

import main
from priority import apply_priority, blocking_points, deadline_points, label_for, model_points

DAY = date(2026, 11, 22)


def task(tid, importance=None, deadline_date=None, prereqs=(), label="Medium", reason=None, **extra):
    row = {"task_id": tid, "priority": label, "explicit_prerequisites": list(prereqs), "inferred_prerequisites": [],
           "deadline_date": deadline_date, "llm_importance": importance, "priority_reason": reason}
    row.update(extra)
    return row


def test_deadline_points_get_bigger_as_the_deadline_gets_closer():
    assert deadline_points("2026-11-22", DAY)[0] == 35
    assert deadline_points("2026-11-24", DAY)[0] == 32
    assert deadline_points("2026-11-27", DAY)[0] == 22
    assert deadline_points("2026-12-05", DAY)[0] == 14
    assert deadline_points("2027-03-01", DAY)[0] == 3
    assert deadline_points(None, DAY) == (0, "no exact deadline")
    assert deadline_points("junk", DAY)[0] == 0


def test_blocking_points():
    assert blocking_points(0, False)[0] == 0
    assert blocking_points(1, False)[0] == 12
    assert blocking_points(2, False)[0] == 20
    assert blocking_points(5, False)[0] == 26
    assert blocking_points(5, True)[0] == 30          # capped
    assert "circular" in blocking_points(1, True)[1]


def test_model_points_use_the_number_then_the_label():
    assert model_points({"llm_importance": 10})[0] == 35
    assert model_points({"llm_importance": 0})[0] == 0
    assert model_points({"llm_importance": 99})[0] == 35            # clamped
    assert model_points({"priority": "High"})[0] == 28              # no number: label stands in
    assert model_points({})[0] == 0
    assert "demo" in model_points({"llm_importance": 7, "priority_reason": "needed for the demo"})[1]


def test_labels_follow_the_score():
    assert [label_for(s) for s in (0, 29, 30, 54, 55, 100)] == ["Low", "Low", "Medium", "Medium", "High", "High"]


def test_a_task_that_blocks_others_outranks_an_equal_task_that_blocks_nothing():
    rows = apply_priority([
        task("T1", importance=5),
        task("T2", importance=5, prereqs=["T1"]),
        task("T3", importance=5, prereqs=["T2"]),
        task("T4", importance=5),
    ], DAY)
    scores = {r["task_id"]: r["priority_score"] for r in rows}
    assert scores["T1"] > scores["T2"] > scores["T3"]
    assert scores["T4"] == scores["T3"]
    assert rows[0]["priority_breakdown"]["dependencies"]["why"] == "blocks 2 tasks"   # transitive


def test_cycles_add_points_and_unknown_prerequisites_are_ignored():
    rows = apply_priority([
        task("T1", importance=5, prereqs=["T2", "Waiting on AWS"]),
        task("T2", importance=5, prereqs=["T1"]),
    ], DAY)
    assert "circular" in rows[0]["priority_breakdown"]["dependencies"]["why"]
    assert rows[0]["priority_breakdown"]["dependencies"]["points"] == 16     # blocks 1 (12) + cycle (4)


def test_final_priority_is_the_score_and_the_model_label_is_kept():
    rows = apply_priority([task("T1", importance=9, deadline_date="2026-11-23", label="Low")], DAY)
    row = rows[0]
    assert row["priority_model"] == "Low"
    assert row["priority"] == "High"              # 32 deadline + 0 blocking + 32 model = 64
    assert row["priority_score"] == 64
    parts = row["priority_breakdown"]
    assert sum(p["points"] for p in parts.values()) == 64
    assert apply_priority([row], DAY)[0]["priority_model"] == "Low"        # idempotent


def test_old_meetings_get_scores_when_read():
    doc = {"date": "2026-11-22T19:41:00+05:30",
           "enriched_tasks": [{"task_id": "T1", "priority": "High", "deadline": "within three days",
                               "explicit_prerequisites": [], "inferred_prerequisites": []}]}
    main.with_exact_deadlines(doc)
    task_ = doc["enriched_tasks"][0]
    assert task_["priority_score"] == 28 + 27       # model label stands in (28) + due in 3 days (27)
    assert task_["priority"] == "High"
