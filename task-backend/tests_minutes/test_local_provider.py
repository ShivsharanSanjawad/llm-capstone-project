"""The local (LM Studio) provider is additive: it is only used when chosen, and Groq and Gemini requests are unchanged."""
import asyncio
import json

import httpx
import pytest

import main
from minutes_module.config import Settings
from minutes_module.llm_service import LLMService, LOCAL_DEFAULT_BASE_URL
from models import FactualExtraction


def reply(content='{"meeting_title":"t","date":"d","tasks":[]}'):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def service(provider, monkeypatch, model="", env=None):
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    svc = LLMService(Settings(provider=provider, model=model))
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        return httpx.Response(200, json=reply())

    svc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return svc, sent


def call(svc):
    record = {"calls": 0, "retries": 0, "input_tokens": 0, "output_tokens": 0, "estimated_input_tokens": 0,
              "estimated_output_tokens": 0, "usage_complete": True, "request_id": "r", "meeting_id": "x"}
    return asyncio.new_event_loop().run_until_complete(
        svc.generate("Extract.", {"transcript": "Priya: I will do it."}, record, schema=FactualExtraction, system="SYS"))


def test_the_local_provider_needs_no_key_and_defaults_to_lm_studio(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_LLM_MODEL", raising=False)
    svc = LLMService(Settings(provider="local"))
    assert svc.configured is True and svc.provider == "local"
    assert svc.base_url == LOCAL_DEFAULT_BASE_URL == "http://127.0.0.1:1234/v1"
    assert svc.model == "local-model"


def test_local_requests_go_to_the_local_server_with_a_schema_constrained_answer(monkeypatch):
    svc, sent = service("local", monkeypatch, env={"LOCAL_LLM_BASE_URL": "http://localhost:5555/v1/", "LOCAL_LLM_MODEL": "qwen3-4b-instruct-2507"})
    call(svc)
    request = sent[0]
    assert request["url"] == "http://localhost:5555/v1/chat/completions"
    body = request["body"]
    assert body["model"] == "qwen3-4b-instruct-2507"
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["name"] == "FactualExtraction"
    assert "reasoning_effort" not in body and "reasoning_format" not in body       # cloud-only settings stay off


def test_local_server_gets_a_placeholder_token_and_a_long_timeout(monkeypatch):
    monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)
    svc = LLMService(Settings(provider="local"))
    assert svc.client.headers["authorization"] == "Bearer lm-studio"
    assert svc.client.timeout.read >= 900                                  # a small local model can take minutes per call
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "real-token")
    assert LLMService(Settings(provider="local")).client.headers["authorization"] == "Bearer real-token"


def test_qwen3_is_told_not_to_think_and_this_can_be_switched_off(monkeypatch):
    svc, sent = service("local", monkeypatch, env={"LOCAL_LLM_MODEL": "qwen3-4b"})
    call(svc)
    assert sent[0]["body"]["messages"][0]["content"].startswith("SYS\n/no_think")
    svc, sent = service("local", monkeypatch, env={"LOCAL_LLM_MODEL": "qwen3-4b", "LOCAL_LLM_NO_THINK": "false"})
    call(svc)
    assert "/no_think" not in sent[0]["body"]["messages"][0]["content"]
    svc, sent = service("local", monkeypatch, env={"LOCAL_LLM_MODEL": "llama-3.1-8b"})      # other models get no switch
    call(svc)
    assert "/no_think" not in sent[0]["body"]["messages"][0]["content"]


def test_groq_and_gemini_requests_are_unchanged(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("GEMINI_API_KEY", "mk")
    svc, sent = service("groq", monkeypatch, model="openai/gpt-oss-120b")
    call(svc)
    body = sent[0]["body"]
    assert sent[0]["url"].startswith("https://api.groq.com/openai/v1/chat/completions")
    assert body["response_format"] == {"type": "json_object"} and body["reasoning_effort"] == "low" and body["reasoning_format"] == "hidden"
    assert "/no_think" not in body["messages"][0]["content"]
    svc, sent = service("gemini", monkeypatch, model="gemini-3.5-flash-lite")
    call(svc)
    body = sent[0]["body"]
    assert sent[0]["url"].startswith("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
    assert body["response_format"] == {"type": "json_object"} and body["reasoning_effort"] == "low"   # Gemini default


def test_gemini_reasoning_effort_is_configurable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mk")
    for value, expected in (("high", "high"), ("medium", "medium"), ("nonsense", "low")):
        monkeypatch.setenv("GEMINI_REASONING_EFFORT", value)
        svc, sent = service("gemini", monkeypatch, model="gemini-3.5-flash-lite")
        call(svc)
        assert sent[0]["body"]["reasoning_effort"] == expected


def test_local_is_never_chosen_automatically():
    keys = {"gemini": None, "groq": None, "openai": None}
    assert main.pick_provider("", keys) == ""                     # no cloud key and no explicit choice: nothing, not local
    assert main.pick_provider("", {"gemini": "g", "groq": "q", "openai": None}) == "gemini"
    assert main.pick_provider("local", keys) == "local"
    assert main.pick_provider("LOCAL ", {"gemini": "g", "groq": "q", "openai": None}) == "local"


def test_strip_thinking_keeps_only_the_json_answer():
    from minutes_module.llm_service import _strip_thinking
    assert _strip_thinking('<think>let me see\n{"a":1} maybe</think>\n{"b": 2}') == '{"b": 2}'
    assert _strip_thinking('no tags {"x": 1} trailing') == '{"x": 1}'
    assert _strip_thinking('<think>ran out of budget and never answered') == ""
    assert _strip_thinking('') == ""


def test_thinking_mode_drops_the_strict_schema_asks_for_brief_thought_and_reads_past_the_reasoning(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_THINKING", "on")
    monkeypatch.setenv("LOCAL_LLM_THINKING_WORDS", "200")
    svc, sent = service("local", monkeypatch, env={"LOCAL_LLM_MODEL": "qwen3-4b"})

    def handler(request):
        sent.append({"body": json.loads(request.content)})
        return httpx.Response(200, json=reply('<think>hmm, no tasks here</think>{"meeting_title":"t","date":"d","tasks":[]}'))

    svc.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = call(svc)
    assert result.meeting_title == "t"
    body = sent[-1]["body"]
    assert "response_format" not in body
    system = body["messages"][0]["content"]
    assert "/think" in system and "200 words" in system and "/no_think" not in system


def test_thinking_mode_is_off_by_default_and_never_applies_to_cloud_providers(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_THINKING", "on")
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    svc, sent = service("groq", monkeypatch, model="openai/gpt-oss-120b")
    call(svc)
    body = sent[0]["body"]
    assert body["response_format"] == {"type": "json_object"} and "/think" not in body["messages"][0]["content"]
