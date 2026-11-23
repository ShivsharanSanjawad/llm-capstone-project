import asyncio
import json
import re
import os
import hashlib
import logging
import random
import time
import weakref
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
import httpx
from pydantic import ValidationError
from .prompt_templates import SYSTEM, PROMPT_VERSION
from .schemas import Minutes
from .transcript_processor import estimate

class LLMError(Exception):
    def __init__(self, code, status=502, diagnostics=None):
        self.code, self.status = code, status
        self.diagnostics = diagnostics or {}
        super().__init__(code)

LOCAL_PROVIDER = "local"
LOCAL_DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"      # LM Studio's OpenAI-compatible server

PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-3.5-flash-lite"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
}

class RequestScheduler:
    """Conservative rolling-window admission; one scheduler per service/event loop.

    Reserve estimated input + output budget before dispatch. Reconcile measured
    usage after success. Failed attempts retain reservations because usage is unknown.
    """
    def __init__(self, settings, clock=time.monotonic, sleep=asyncio.sleep):
        self.settings, self.clock, self.sleep = settings, clock, sleep
        self.slots = asyncio.Semaphore(settings.max_concurrency)
        self.lock = asyncio.Lock()
        self.window = deque()

    @asynccontextmanager
    async def reserve(self, tokens, record):
        if tokens > self.settings.tokens_per_minute:
            raise LLMError("request_exceeds_token_rate_budget", 422)
        started = self.clock()
        async with self.slots:
            while True:
                async with self.lock:
                    now = self.clock()
                    while self.window and self.window[0][0] <= now - 60:
                        self.window.popleft()
                    if len(self.window) < self.settings.requests_per_minute and sum(row[1] for row in self.window)+tokens <= self.settings.tokens_per_minute:
                        ticket = [now, tokens]
                        self.window.append(ticket)
                        break
                    wait = max(.001, self.window[0][0]+60-now)
                await self.sleep(wait)
            record["scheduler_wait_seconds"] = round(record.get("scheduler_wait_seconds", 0)+self.clock()-started, 3)
            yield ticket


def generation_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()).hexdigest()


def _compact_schema(node):
    """Drop the auto-generated display titles ("Task Id", ...) from a JSON schema: they cost tokens on every call and say nothing the
    field names do not. A property that is itself called "title" has a dict value and is kept."""
    if isinstance(node, dict):
        return {k: _compact_schema(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(node, list):
        return [_compact_schema(v) for v in node]
    return node


def _local_thinking() -> bool:
    """LOCAL_LLM_THINKING=on lets a local reasoning model (Qwen3) think before answering, within a word budget."""
    return (os.getenv("LOCAL_LLM_THINKING") or "off").strip().lower() in {"1", "true", "yes", "on"}


def _strip_thinking(text: str) -> str:
    """Remove a model's <think>...</think> reasoning and return just the JSON object that follows it."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "<think>" in text:                         # reasoning never closed: it ran out of budget before the answer
        return ""
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if 0 <= start < end else text.strip()


class LLMService:
    def __init__(self, settings, client=None):
        self.settings = settings
        self._loops = weakref.WeakKeyDictionary()
        self._cache = OrderedDict()
        self._json_mode_supported = True
        self.provider = settings.provider or next((p for p, v in PROVIDERS.items() if os.getenv(v[0])), "")
        if self.provider and self.provider not in PROVIDERS and self.provider != LOCAL_PROVIDER:
            raise ValueError("Unsupported minutes provider")
        key, base, default = PROVIDERS.get(self.provider, ("", None, ""))
        self.model = settings.model or os.getenv("LLM_MODEL") or default
        self.configured = bool(os.getenv(key))
        self.base_url = (base or "").rstrip("/")
        self._sdk_compat = client is not None
        self.client = client
        if self.provider == LOCAL_PROVIDER:
            # A model served on this machine (LM Studio). Chosen only explicitly (LLM_PROVIDER=local); never auto-detected.
            self.base_url = (os.getenv("LOCAL_LLM_BASE_URL") or LOCAL_DEFAULT_BASE_URL).rstrip("/")
            self.model = settings.model or os.getenv("LOCAL_LLM_MODEL") or "local-model"
            self.configured = True
            if client is None:
                # Local models are slow: allow minutes per call. LM Studio ignores the token unless API tokens are switched on.
                self.client = httpx.AsyncClient(
                    timeout=max(settings.timeout_seconds, 900),
                    headers={"Authorization": f"Bearer {os.getenv('LOCAL_LLM_API_KEY') or 'lm-studio'}"},
                )
        elif client is None and self.configured:
            self.client = httpx.AsyncClient(
                timeout=settings.timeout_seconds,
                headers={"Authorization": f"Bearer {os.environ[key]}"},
            )

    async def close(self):
        if self.client:
            close = getattr(self.client, "aclose", None) or getattr(self.client, "close", None)
            if close:
                await close()

    def loop_state(self):
        loop = asyncio.get_running_loop()
        if loop not in self._loops:
            self._loops[loop] = (RequestScheduler(self.settings), {})
        return self._loops[loop]

    def signature(self):
        return {"provider": self.provider, "model": self.model, "prompt_version": PROMPT_VERSION,
            **{key:getattr(self.settings, key) for key in ("temperature", "max_output_tokens", "output_token_ceiling", "context_tokens", "chunk_tokens", "enable_verification", "enable_semantic_review", "length_retries")}}

    async def generate(self, instruction, payload, record, schema=Minutes, system=SYSTEM):
        key = generation_key([self.signature(), schema.model_json_schema(), system, instruction, payload])
        now = time.monotonic()
        cached = self._cache.get(key)
        if not record.get("force_refresh") and cached and cached[0] > now:
            self._cache.move_to_end(key)
            record["cache_hits"] = record.get("cache_hits", 0)+1
            logging.getLogger("minutes").info(json.dumps({"event":"llm_cache_hit", "request_id":record.get("request_id"), "provider":self.provider, "model":self.model}))
            return schema.model_validate_json(cached[1])
        if cached and cached[0] <= now:
            self._cache.pop(key, None)
        _, jobs = self.loop_state()
        if key in jobs:
            record["coalesced_calls"] = record.get("coalesced_calls", 0)+1
            result = await asyncio.shield(jobs[key])
            return result.model_copy(deep=True)
        task = asyncio.create_task(self._generate(instruction, payload, record, schema, system))
        jobs[key] = task
        try:
            result = await task
            if self.settings.cache_ttl_seconds and self.settings.cache_entries:
                self._cache[key] = (time.monotonic()+self.settings.cache_ttl_seconds, result.model_dump_json())
                self._cache.move_to_end(key)
                while len(self._cache) > self.settings.cache_entries:
                    self._cache.popitem(last=False)
            return result
        finally:
            if jobs.get(key) is task:
                jobs.pop(key, None)

    @staticmethod
    def _completion_from_json(data):
        usage_data = data.get("usage")
        usage = SimpleNamespace(
            prompt_tokens=usage_data.get("prompt_tokens"),
            completion_tokens=usage_data.get("completion_tokens"),
        ) if isinstance(usage_data, dict) else None
        choices = data.get("choices") or []
        if not choices:
            raise ValueError("Provider returned no completion choices.")
        choice = choices[0]
        message = choice.get("message") or {}
        return SimpleNamespace(
            usage=usage,
            choices=[SimpleNamespace(
                finish_reason=choice.get("finish_reason"),
                message=SimpleNamespace(
                    content=message.get("content"),
                    refusal=message.get("refusal"),
                ),
            )],
        )

    @staticmethod
    def _error_details(error):
        response = getattr(error, "response", None)
        status_code = getattr(error, "status_code", None)
        if status_code is None and response is not None:
            status_code = response.status_code
        body = getattr(error, "body", None)
        if not isinstance(body, dict) and response is not None:
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError):
                body = {}
        return status_code, body if isinstance(body, dict) else {}, response.headers if response is not None else {}

    async def _generate(self, instruction, payload, record, schema=Minutes, system=SYSTEM):
        if not self.client:
            raise LLMError("provider_not_configured", 503)
        if self.provider == LOCAL_PROVIDER and _local_thinking():
            words = (os.getenv("LOCAL_LLM_THINKING_WORDS") or "300").strip()
            system = system + f"\n/think\nThink briefly: use at most about {words} words of reasoning, then give only the final JSON answer."
        elif self.provider == LOCAL_PROVIDER and "qwen3" in self.model.lower() and (os.getenv("LOCAL_LLM_NO_THINK") or "true").strip().lower() not in {"0", "false", "no", "off"}:
            system = system + "\n/no_think"        # Qwen3's switch for skipping its long hidden reasoning
        messages = [{"role":"system", "content":system+"\nJSON schema: "+json.dumps(_compact_schema(schema.model_json_schema()))},
                    {"role":"user", "content":instruction+"\n"+json.dumps(payload, ensure_ascii=False)}]
        if estimate(json.dumps(messages, ensure_ascii=False)) + self.settings.max_output_tokens > self.settings.context_tokens:
            raise LLMError("request_exceeds_context_budget", 422)
        input_estimate = estimate(json.dumps(messages, ensure_ascii=False))
        output_limit = min(self.settings.max_output_tokens, self.settings.tokens_per_minute-input_estimate)
        if schema.__name__ == "ReviewResult":
            output_limit = min(output_limit, max(256, len(payload.get("claims", []))*48+128))
        if output_limit < 128:
            raise LLMError("request_exceeds_token_rate_budget", 422)
        ceiling = min(max(output_limit, self.settings.output_token_ceiling), self.settings.context_tokens-input_estimate, self.settings.tokens_per_minute-input_estimate)
        mode, retry, length_retry = self._json_mode_supported, 0, 0
        json_generation_retry = False
        while True:
            if record["calls"] >= self.settings.max_calls:
                raise LLMError("llm_call_limit", 422)
            kwargs = dict(model=self.model, messages=messages, temperature=self.settings.temperature,
                          max_tokens=output_limit)
            if self.provider == "groq" and self.model in {"openai/gpt-oss-20b", "openai/gpt-oss-120b"}:
                # Groq's GPT-OSS reasoning must not leak into JSON output. Use
                # the documented low/hidden settings only for these models.
                kwargs["reasoning_effort"] = record.get("reasoning_effort", "low")
                kwargs["extra_body"] = {"reasoning_format": "hidden"}
            if self.provider == "gemini":
                # Gemini 3 "thinks" before answering, which made every call 20-80 seconds. Low effort keeps the quality for
                # extraction and classification at a fraction of the time and output tokens.
                kwargs["reasoning_effort"] = record.get("reasoning_effort", "low")
            if self.provider == LOCAL_PROVIDER and _local_thinking():
                pass        # a strict schema forces JSON from the first character, which leaves no room to think first
            elif self.provider == LOCAL_PROVIDER:
                # LM Studio can force the answer to match the schema (grammar-constrained decoding), which matters a lot for
                # small models. It does not accept the json_object mode the other providers use.
                kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "strict": True, "schema": _compact_schema(schema.model_json_schema())}}
            elif mode:
                kwargs["response_format"] = {"type":"json_object"}
            try:
                scheduler, _ = self.loop_state()
                async with scheduler.reserve(input_estimate+output_limit, record) as ticket:
                    record["calls"] += 1
                    record["estimated_input_tokens"] += input_estimate
                    if self._sdk_compat:
                        response = await self.client.chat.completions.create(**kwargs)
                    else:
                        request_body = dict(kwargs)
                        request_body.update(request_body.pop("extra_body", {}))
                        http_response = await self.client.post(
                            f"{self.base_url}/chat/completions",
                            json=request_body,
                        )
                        http_response.raise_for_status()
                        response = self._completion_from_json(http_response.json())
                    usage = response.usage
                    if usage and usage.prompt_tokens is not None and usage.completion_tokens is not None:
                        ticket[1] = usage.prompt_tokens+usage.completion_tokens
                usage = response.usage
                if usage and usage.prompt_tokens is not None and usage.completion_tokens is not None:
                    record["input_tokens"] += usage.prompt_tokens
                    record["output_tokens"] += usage.completion_tokens
                else:
                    record["usage_complete"] = False
                choice = response.choices[0]
                logging.getLogger("minutes").info(json.dumps({"event":"llm_attempt", "request_id":record.get("request_id"), "provider":self.provider, "model":self.model,
                    "attempt":record["calls"], "finish_reason":choice.finish_reason, "estimated_input_tokens":input_estimate, "output_limit":output_limit,
                    "reported_input_tokens":usage.prompt_tokens if usage else None, "reported_output_tokens":usage.completion_tokens if usage else None}))
                content = choice.message.content or ""
                if self.provider == LOCAL_PROVIDER and _local_thinking():
                    content = _strip_thinking(content)
                record["estimated_output_tokens"] += estimate(content)
                if choice.finish_reason == "length" and not getattr(choice.message, "refusal", None):
                    # Restart the same request with a bounded larger budget. Never append
                    # a partial JSON object or treat it as a successful response.
                    if length_retry < self.settings.length_retries and output_limit < ceiling:
                        output_limit = min(output_limit * 2, ceiling)
                        length_retry += 1
                        record["retries"] += 1
                        continue
                if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None):
                    raise LLMError("incomplete_or_refused_output", diagnostics={"finish_reason": choice.finish_reason})
                try:
                    return schema.model_validate_json(content)
                except ValidationError as exc:
                    # Report error categories only, never generated text or transcript values.
                    kinds = sorted({error["type"] for error in exc.errors(include_input=False, include_context=False)})
                    raise LLMError("invalid_structured_output", diagnostics={"validation_error_types": kinds}) from None
            except LLMError:
                raise
            except Exception as exc:
                status_code, body, response_headers = self._error_details(exc)
                if status_code is None:
                    if isinstance(exc, httpx.RequestError) or type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}:
                        record["usage_complete"] = False
                        category = "provider_connection_timeout"
                        delay = 2 ** retry + random.uniform(0, self.settings.retry_jitter * 2 ** retry)
                    else:
                        raise
                else:
                    record["usage_complete"] = False
                detail = body.get("error", body)
                param = detail.get("param", "") if isinstance(detail, dict) else ""
                message = str(detail.get("message", "")) if isinstance(detail, dict) else ""
                provider_code = str(detail.get("code", "")) if isinstance(detail, dict) else ""
                json_generation_failed = provider_code == "json_validate_failed" or "failed to generate json" in message.lower()
                if status_code == 400 and self.provider == "groq" and mode and json_generation_failed and not json_generation_retry:
                    # This is a failed model output, not an unsupported model.
                    # Retry once with prompted JSON; the exact same Pydantic
                    # schema and deterministic evidence checks still apply.
                    mode = False
                    json_generation_retry = True
                    record["retries"] += 1
                    logging.getLogger("minutes").warning(json.dumps({"event":"json_generation_fallback", "request_id":record.get("request_id"), "provider":self.provider, "model":self.model}))
                    continue
                format_unsupported = param == "response_format" or ("response_format" in message.lower() and any(word in message.lower() for word in ("unsupported", "not supported", "not available", "unknown parameter")))
                if status_code in (400, 422) and mode and format_unsupported:
                    mode = False
                    self._json_mode_supported = False
                    continue
                if status_code is None:
                    pass
                else:
                    category = classify_provider_error(status_code, detail)
                    logging.getLogger("minutes").warning(json.dumps({"event":"provider_error", "request_id":record.get("request_id"), "provider":self.provider, "model":self.model, "upstream_status":status_code, "category":category}))
                if category in {"provider_quota_exhausted", "provider_context_limit", "provider_authentication", "provider_model_unavailable"}:
                    raise LLMError(category, 429 if category=="provider_quota_exhausted" else 422 if category=="provider_context_limit" else 502,
                        diagnostics={"upstream_status":status_code}) from None
                if status_code is not None:
                    transient = status_code in (408, 409, 429) or status_code >= 500
                    if not transient:
                        raise LLMError("provider_authentication" if status_code in (401,403) else "provider_invalid_request",
                            diagnostics={"upstream_status": status_code,
                                "parameter": param if param in {"model", "response_format", "temperature", "max_tokens", "max_completion_tokens", "reasoning_effort", "reasoning_format"} else None,
                                "provider_code": provider_code if provider_code in {"json_validate_failed", "invalid_request_error", "invalid_parameter", "unsupported_parameter"} else None}) from None
                    delay = 2 ** retry + random.uniform(0, self.settings.retry_jitter * 2 ** retry)
                    header = response_headers.get("retry-after")
                    if header:
                        try:
                            delay = max(delay, float(header))
                        except ValueError:
                            try:
                                delay = max(delay, (parsedate_to_datetime(header)-datetime.now(timezone.utc)).total_seconds())
                            except (ValueError, TypeError):
                                pass
            if retry >= self.settings.max_retries:
                raise LLMError("provider_transient_failure", 504, diagnostics={"category":category})
            retry += 1
            record["retries"] += 1
            record["usage_complete"] = False
            await asyncio.sleep(delay)


def classify_provider_error(status, detail):
    detail = detail if isinstance(detail, dict) else {}
    code = str(detail.get("code", "")).lower()
    kind = str(detail.get("type", "")).lower()
    text = str(detail.get("message", "")).lower()
    combined = " ".join((code, kind, text))
    if any(marker in combined for marker in ("insufficient_quota", "quota_exceeded", "billing_hard_limit", "spend_limit", "credit_balance", "daily limit", "tokens per day", "requests per day", "perday", "per_day", "current quota", "plan and billing", "daily token", "daily request", "per day")):
        return "provider_quota_exhausted"
    if any(word.strip(":,().") in {"tpd", "rpd"} for word in combined.split()):
        return "provider_quota_exhausted"
    if status in (401, 403):
        return "provider_authentication"
    if status == 404 or code in {"model_not_found", "model_decommissioned"}:
        return "provider_model_unavailable"
    if any(marker in combined for marker in ("context_length_exceeded", "context window", "maximum context", "too many input tokens")):
        return "provider_context_limit"
    if status == 429:
        if kind == "tokens" or any(marker in combined for marker in ("tokens per minute", "tpm", "token rate", "tokensperminute")):
            return "provider_token_rate_limit"
        if kind == "requests" or any(marker in combined for marker in ("requests per minute", "rpm", "request rate", "requestsperminute")):
            return "provider_request_rate_limit"
        return "provider_rate_limit"
    return "provider_server_error" if status >= 500 else "provider_invalid_request"
