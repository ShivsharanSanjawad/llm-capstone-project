"""The process endpoint turns spoken deadlines into exact dates counted from the meeting day."""
import asyncio
from types import SimpleNamespace

import main
from models import ContextualInference, EnrichedTask, ExplicitTask, FactualExtraction
from main import MeetingProcessRequest, is_valid_participant_name


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_participant_filter_rejects_meet_interface_sentences():
    assert not is_valid_participant_name("Everyone can see your annotations")
    assert is_valid_participant_name("Madhukar Pai")


def test_process_adds_exact_dates_from_the_meeting_day(monkeypatch):
    seen = {}

    async def factual(text, meeting_date=None):
        seen["factual_date"] = meeting_date
        return FactualExtraction(meeting_title="t", date="d", tasks=[
            ExplicitTask(task_id="T1", description="Build UI", explicit_deadline="within five days")])

    async def inference(text, tasks, meeting_date=None):
        seen["inference_date"] = meeting_date
        return ContextualInference(enriched_tasks=[EnrichedTask(
            task_id="T1", description="Build UI", owner="A", missing_owner=False, deadline="within five days",
            missing_deadline=False, priority="High", explicit_prerequisites=[])])

    saved = {}

    async def save(doc):
        saved.update(doc)
        return "m1"

    async def update(meeting_id, updates):
        saved.update(updates)
        return True

    monkeypatch.setattr(main, "run_factual_extraction", factual)
    monkeypatch.setattr(main, "run_contextual_inference", inference)
    monkeypatch.setattr(main, "save_meeting_doc", save)
    monkeypatch.setattr(main, "update_meeting_doc", update)
    request = MeetingProcessRequest(
        transcript_text="Priya: I will build the UI within five days.",
        meeting={"title": "m", "startedAt": "2026-11-22T19:41:00+05:30", "participants": []})
    result = run(main.process_meeting_from_extension(request))
    assert seen == {"factual_date": "2026-11-22", "inference_date": "2026-11-22"}
    assert saved["tasks"][0]["deadline_method"] == "calendar rule"
    task = result["tasks"][0]
    assert task["deadline"] == "within five days"
    assert task["deadline_date"] == "2026-11-27"
    assert task["deadline_label"] == "Fri, 27 Nov 2026"
    assert saved["tasks"][0]["deadline_date"] == "2026-11-27"
    assert saved["enriched_tasks"][0]["deadline_date"] == "2026-11-27"


def test_older_meetings_get_exact_dates_on_read():
    doc = {"date": "2026-11-22T19:41:00+05:30",
           "tasks": [{"task_id": "T1", "explicit_deadline": "within seven days"}],
           "enriched_tasks": [{"task_id": "T1", "deadline": "within seven days"}]}
    main.with_exact_deadlines(doc)
    assert doc["enriched_tasks"][0]["deadline_date"] == "2026-11-29"
    assert doc["tasks"][0]["deadline_label"] == "Sun, 29 Nov 2026"


def test_inference_payload_carries_the_calendar_and_the_prompt_asks_for_dates(monkeypatch):
    captured = {}

    async def fake(kind, system, instruction, payload, model):
        captured.update(system=system, payload=payload)
        return ContextualInference(enriched_tasks=[])

    monkeypatch.setattr(main, "structured_task_call", fake)
    run(main.run_contextual_inference("text", [], "2026-11-22"))
    assert captured["payload"]["meeting_date"] == "2026-11-22"
    assert captured["payload"]["meeting_weekday"] == "Sunday"
    assert captured["payload"]["calendar"].startswith("Sun 2026-11-22; Mon 2026-11-23")
    assert "deadline_date" in captured["system"] and "YYYY-MM-DD" in captured["system"]
