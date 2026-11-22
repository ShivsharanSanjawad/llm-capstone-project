import json
import logging
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
from fastapi import HTTPException
from database import database
from .pricing import estimate_cost, load_overrides
logger = logging.getLogger("minutes")

def utcnow():
    return datetime.now(timezone.utc).isoformat()

class Monitor:
    def __init__(self, collection=None, pricing_file=None):
        self._collection = collection
        self._pricing_file = pricing_file

    @property
    def collection(self):
        return self._collection if self._collection is not None else database.collection("llm_monitoring")

    def begin(self, meeting_id, provider, model):
        return dict(request_id=str(uuid4()), meeting_id=meeting_id, provider=provider, model=model,
                    started_at=utcnow(), calls=0, retries=0, input_tokens=0, output_tokens=0,
                    estimated_input_tokens=0, estimated_output_tokens=0, usage_complete=True,
                    chunk_count=0, status="processing", _clock=monotonic())

    async def finish(self, record, status, error=None):
        if "_clock" not in record:
            return
        record.update(status=status, error_type=error, completed_at=utcnow(), duration_seconds=round(monotonic()-record.pop("_clock"), 3))
        logger.info(json.dumps(record))
        try:
            await self.collection.replace_one({"_id":record["request_id"]}, dict(record), upsert=True)
        except Exception:
            raise HTTPException(503, detail={"code":"monitoring_persistence_failed", "meeting_id":record["meeting_id"]}) from None

    async def stats(self):
        try:
            cursor = self.collection.find({}, {"_id":0}).sort("started_at", -1).limit(1000)
            rows = [row async for row in cursor]
            # Price every row from its stored token counts (works for history saved before pricing existed).
            overrides = load_overrides(self._pricing_file)
            for row in rows:
                priced = estimate_cost(row, overrides)
                if priced:
                    row.update(priced)
            priced_rows = [r for r in rows if r.get("estimated_cost_usd") is not None]
            unpriced = len(rows) - len(priced_rows)
            estimated_rows = sum(r.get("cost_basis") == "estimated_tokens" for r in priced_rows)
            return dict(scope="latest 1000 persisted generation requests", requests=len(rows),
                succeeded=sum(r["status"]=="succeeded" for r in rows), failed=sum(r["status"]=="failed" for r in rows),
                llm_calls=sum(r["calls"] for r in rows), retries=sum(r["retries"] for r in rows), recent=rows[:50],
                average_duration_seconds=sum(r["duration_seconds"] for r in rows)/len(rows) if rows else None,
                input_tokens=sum(r["input_tokens"] for r in rows), output_tokens=sum(r["output_tokens"] for r in rows),
                usage_complete=all(r["usage_complete"] for r in rows),
                estimated_cost_usd=sum(r["estimated_cost_usd"] for r in priced_rows) if priced_rows else None,
                cost_priced_requests=len(priced_rows), cost_unpriced_requests=unpriced,
                cost_estimated_requests=estimated_rows,
                cost_note="Paid-tier list prices; free tiers bill $0. Cached-input discounts are not applied.")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, detail={"code":"monitoring_storage_unavailable"}) from None
