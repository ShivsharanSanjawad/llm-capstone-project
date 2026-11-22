import asyncio
from minutes_module.monitoring import Monitor
from minutes_module.pricing import estimate_cost, find_rate


def row(**kw):
    base = dict(provider="groq", model="openai/gpt-oss-20b", usage_complete=True, input_tokens=12000, output_tokens=3000,
                estimated_input_tokens=0, estimated_output_tokens=0, status="succeeded", calls=1, retries=0, duration_seconds=1.0)
    base.update(kw)
    return base


def test_cost_uses_provider_reported_tokens_at_list_price():
    priced = estimate_cost(row())
    assert round(priced["estimated_cost_usd"], 6) == 0.0018  # 12k*$0.075/M + 3k*$0.30/M
    assert priced["cost_basis"] == "provider_reported"
    assert round(estimate_cost(row(model="openai/gpt-oss-120b"))["estimated_cost_usd"], 6) == 0.0036


def test_cost_falls_back_to_estimated_tokens_and_says_so():
    priced = estimate_cost(row(usage_complete=False, input_tokens=0, output_tokens=0,
                               estimated_input_tokens=5000, estimated_output_tokens=1000))
    assert round(priced["estimated_cost_usd"], 6) == 0.000675
    assert priced["cost_basis"] == "estimated_tokens"


def test_unknown_model_has_no_cost_and_dated_variants_match():
    assert estimate_cost(row(model="not-a-real-model")) is None
    rate, _ = find_rate("gemini", "models/gemini-2.5-flash-preview-09-2025")
    assert rate["output_per_million"] == 2.50


def test_pricing_file_overrides_builtin():
    override = {"groq/openai/gpt-oss-20b": {"input_per_million": 1, "output_per_million": 2}}
    assert round(estimate_cost(row(), override)["estimated_cost_usd"], 6) == 0.018


class FakeCursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, *a): return self
    def limit(self, *a): return self
    def __aiter__(self):
        async def gen():
            for r in self.rows: yield r
        return gen()


class FakeCollection:
    def __init__(self, rows): self.rows = rows
    def find(self, *a): return FakeCursor(self.rows)


def test_stats_prices_old_rows_and_reports_partial_coverage():
    rows = [row(), row(model="mystery-model"), row(usage_complete=False, input_tokens=0, output_tokens=0,
                                                   estimated_input_tokens=5000, estimated_output_tokens=1000)]
    stats = asyncio.run(Monitor(FakeCollection(rows)).stats())
    assert round(stats["estimated_cost_usd"], 6) == round(0.0018 + 0.000675, 6)
    assert stats["cost_priced_requests"] == 2 and stats["cost_unpriced_requests"] == 1
    assert stats["cost_estimated_requests"] == 1
