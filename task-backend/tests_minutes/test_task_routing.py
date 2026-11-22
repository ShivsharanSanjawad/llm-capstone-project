"""Task extraction/inference must run through the shared, rate-limited LLMService and be tracked for usage and cost."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from minutes_module.config import Settings
from minutes_module.llm_service import LLMService
from models import FactualExtraction, ExplicitTask, ContextualInference, EnrichedTask


def ok(model, prompt=900, completion=300):
    return NS(usage=NS(prompt_tokens=prompt, completion_tokens=completion),
              choices=[NS(finish_reason="stop", message=NS(content=model.model_dump_json(), refusal=None))])


def truncated():
    return NS(usage=NS(prompt_tokens=900, completion_tokens=512),
              choices=[NS(finish_reason="length", message=NS(content='{"tasks":[', refusal=None))])


def service(*responses, **settings_kw):
    sdk = NS(chat=NS(completions=NS(create=AsyncMock(side_effect=list(responses)))))
    settings = Settings(provider="groq", model="openai/gpt-oss-20b", max_output_tokens=512, output_token_ceiling=2048,
                        retry_jitter=0, enable_verification=False, **settings_kw)
    return LLMService(settings, sdk), sdk


def task(task_id="T1"):
    return ExplicitTask(task_id=task_id, description="Fix release bug", explicit_owner="Priya")


def monitoring_rows(fake_database):
    return list(fake_database["llm_monitoring"].collection.find({}))


def test_extraction_uses_shared_service_and_records_usage_and_cost(monkeypatch, fake_database):
    import main
    llm, sdk = service(ok(FactualExtraction(meeting_title="Release", date="", tasks=[task()])))
    monkeypatch.setattr(main, "llm_service", llm)
    result = asyncio.run(main.run_factual_extraction("Priya: I will fix the release bug."))
    assert [t.task_id for t in result.tasks] == ["T1"]
    sent = sdk.chat.completions.create.call_args.kwargs
    assert "strict factual action-item extractor" in sent["messages"][0]["content"]   # the newer prompt is kept
    assert "Priya: I will fix the release bug." in sent["messages"][1]["content"]
    [row] = monitoring_rows(fake_database)
    assert row["meeting_id"] == "task-extraction" and row["status"] == "succeeded"
    assert row["input_tokens"] == 900 and row["output_tokens"] == 300 and row["calls"] == 1


def test_inference_keeps_cycle_and_resolution_fields(monkeypatch, fake_database):
    import main
    enriched = [EnrichedTask(task_id="T1", description="One", owner=None, missing_owner=True, deadline=None,
                             missing_deadline=True, priority="Medium", explicit_prerequisites=["T2"])]
    inferred = ContextualInference(enriched_tasks=enriched,
                                   initial_circular_dependencies=[{"from_task_id": "T2", "to_task_id": "T1", "description": "A needs B"}],
                                   resolved_dependencies=[{"description": "Agreed to share a draft first"}])
    llm, _ = service(ok(inferred))
    monkeypatch.setattr(main, "llm_service", llm)
    result = asyncio.run(main.run_contextual_inference("transcript", [{"task_id": "T1", "description": "One"}]))
    assert result.initial_circular_dependencies[0]["from_task_id"] == "T2"
    assert result.resolved_dependencies == [{"description": "Agreed to share a draft first"}]
    assert monitoring_rows(fake_database)[0]["meeting_id"] == "task-inference"


def test_truncated_answer_is_retried_with_a_larger_budget(monkeypatch, fake_database):
    import main
    llm, sdk = service(truncated(), ok(FactualExtraction(meeting_title="R", date="", tasks=[task()])))
    monkeypatch.setattr(main, "llm_service", llm)
    result = asyncio.run(main.run_factual_extraction("text"))
    assert result.tasks and sdk.chat.completions.create.await_count == 2
    assert [c.kwargs["max_tokens"] for c in sdk.chat.completions.create.call_args_list] == [512, 1024]
    assert monitoring_rows(fake_database)[0]["retries"] == 1


def test_token_rate_limit_is_waited_out_and_retried(monkeypatch, fake_database):
    import main

    class RateLimited(Exception):
        status_code = 429
        response = None
        body = {"error": {"type": "tokens", "code": "rate_limit_exceeded",
                          "message": "Rate limit reached on tokens per minute (TPM): Limit 8000, Used 5061, Requested 2965."}}
    llm, sdk = service(RateLimited(), ok(FactualExtraction(meeting_title="R", date="", tasks=[task()])))
    monkeypatch.setattr(main, "llm_service", llm)
    monkeypatch.setattr("minutes_module.llm_service.asyncio.sleep", AsyncMock())   # do not really wait
    result = asyncio.run(main.run_factual_extraction("text"))
    assert result.tasks and sdk.chat.completions.create.await_count == 2


def test_failure_becomes_a_coded_http_error_and_is_recorded(monkeypatch, fake_database):
    import main
    llm, _ = service(*[truncated()] * 5)
    monkeypatch.setattr(main, "llm_service", llm)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main.run_factual_extraction("text"))
    assert caught.value.detail["code"] == "incomplete_or_refused_output"
    row = monitoring_rows(fake_database)[0]
    assert row["status"] == "failed" and row["error_type"] == "incomplete_or_refused_output"


def test_missing_provider_is_a_clear_503(monkeypatch):
    import main
    monkeypatch.setattr(main, "llm_service", NS(client=None))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main.run_factual_extraction("text"))
    assert caught.value.status_code == 503


def test_monitoring_write_failure_never_fails_the_request(monkeypatch, fake_database):
    import main
    llm, _ = service(ok(FactualExtraction(meeting_title="R", date="", tasks=[task()])))
    monkeypatch.setattr(main, "llm_service", llm)
    monkeypatch.setattr(main.task_monitor, "finish", AsyncMock(side_effect=HTTPException(503)))
    assert asyncio.run(main.run_factual_extraction("text")).tasks
