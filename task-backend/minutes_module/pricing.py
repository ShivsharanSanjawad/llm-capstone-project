"""Estimated API cost from token counts at *paid-tier list prices* (free tiers actually bill $0).

Prices are USD per million tokens. ``checked`` is the date a rate was last verified against the provider's
pricing page; entries without it are well-known defaults that should be re-checked before relying on them.
Override or extend any rate with the JSON pricing file (``MINUTES_PRICING_FILE``), e.g.
{"groq/openai/gpt-oss-20b": {"input_per_million": 0.075, "output_per_million": 0.30}}.
"""
import json
from pathlib import Path

PRICES = {
    # Groq (https://console.groq.com/docs/models). Cached input is 50% cheaper; cached counts are not tracked here,
    # so this is a slight over-estimate when prompt caching applies.
    "groq/openai/gpt-oss-20b":  {"input_per_million": 0.075, "output_per_million": 0.30, "checked": "2026-11-22"},
    "groq/openai/gpt-oss-120b": {"input_per_million": 0.15,  "output_per_million": 0.60, "checked": "2026-11-22"},
    "groq/llama-3.3-70b-versatile": {"input_per_million": 0.59, "output_per_million": 0.79},
    "groq/llama-3.1-8b-instant":    {"input_per_million": 0.05, "output_per_million": 0.08},
    # Google Gemini API, standard tier, prompts up to 200k tokens (https://ai.google.dev/gemini-api/docs/pricing).
    # Output price includes thinking tokens.
    "gemini/gemini-2.5-flash":      {"input_per_million": 0.30, "output_per_million": 2.50, "checked": "2026-11-22"},
    "gemini/gemini-2.5-pro":        {"input_per_million": 1.25, "output_per_million": 10.00, "checked": "2026-11-22"},
    "gemini/gemini-2.5-flash-lite": {"input_per_million": 0.10, "output_per_million": 0.40},
    "gemini/gemini-3.5-flash-lite": {"input_per_million": 0.30, "output_per_million": 2.50, "checked": "2026-11-22"},
    # OpenAI
    "openai/gpt-4o-mini":  {"input_per_million": 0.15, "output_per_million": 0.60},
    "openai/gpt-4.1-mini": {"input_per_million": 0.40, "output_per_million": 1.60},
    "openai/gpt-4o":       {"input_per_million": 2.50, "output_per_million": 10.00},
}

def load_overrides(pricing_file) -> dict:
    try:
        return json.loads(Path(pricing_file).read_text()) if pricing_file else {}
    except Exception:
        return {}

def find_rate(provider: str, model: str, overrides: dict | None = None):
    """Return (rate, source) or (None, None). Matches exactly, then the longest key that prefixes a dated variant."""
    key = f"{(provider or '').lower()}/{(model or '').lower().removeprefix('models/')}"
    for table, label in ((overrides or {}, "pricing file"), (PRICES, "built-in list price")):
        table = {k.lower(): v for k, v in table.items()}
        if key in table:
            rate = table[key]
        else:
            matches = [k for k in table if key.startswith(k + "-") or key.startswith(k + ":")]
            rate = table[max(matches, key=len)] if matches else None
        if rate:
            checked = rate.get("checked")
            if label == "pricing file":
                return rate, label
            return rate, f"{label}{', checked ' + checked if checked else ', not re-checked'}"
    return None, None

def estimate_cost(row: dict, overrides: dict | None = None) -> dict | None:
    """Cost for one monitoring row. Prefers provider-reported tokens, else the byte-based token estimates."""
    rate, source = find_rate(row.get("provider"), row.get("model"), overrides)
    if rate is None:
        return None
    reported = bool(row.get("usage_complete")) and (row.get("input_tokens") or row.get("output_tokens"))
    if reported:
        tokens_in, tokens_out, basis = row.get("input_tokens") or 0, row.get("output_tokens") or 0, "provider_reported"
    else:
        tokens_in, tokens_out, basis = row.get("estimated_input_tokens") or 0, row.get("estimated_output_tokens") or 0, "estimated_tokens"
        if not (tokens_in or tokens_out):   # nothing was sent (e.g. rejected before any call)
            tokens_in = tokens_out = 0
            basis = "provider_reported"
    cost = (tokens_in * rate["input_per_million"] + tokens_out * rate["output_per_million"]) / 1_000_000
    return {"estimated_cost_usd": cost, "cost_basis": basis, "pricing_source": source}
