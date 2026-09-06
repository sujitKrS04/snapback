"""
FastAPI Booking Backend

Endpoints:
  POST /check-availability  {date, request_id} -> {available_slots, request_id}  (default 4s artificial delay)
  POST /book                {date, slot, request_id} -> confirmation
  GET  /health              -> {status: ok}

Every inbound request and outbound response is logged as a JSON line to
/logs/backend.log (with local fallback to logs/backend.log).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Optional
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DELAY: float = float(os.getenv("CHECK_AVAILABILITY_DELAY", "4.0"))
BACKEND_LOG_PATH: str = os.getenv("BACKEND_LOG_PATH", "logs/backend.log")

# Candidate log paths — write to first that succeeds
_LOG_CANDIDATES: list[Path] = [
    Path("/logs/backend.log"),
    Path(BACKEND_LOG_PATH),
]

# Pre-generated deterministic slots (not random so responses are stable)
_AVAILABLE_SLOTS: list[str] = ["09:00", "10:30", "12:00", "14:00", "15:30", "17:00"]

# ---------------------------------------------------------------------------
# Structured JSON logger
# ---------------------------------------------------------------------------

_std_logger = logging.getLogger("snapback-backend")
_std_logger.setLevel(logging.INFO)
if not _std_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    _std_logger.addHandler(_h)


def _write_log(record: dict[str, Any]) -> None:
    """Write a structured JSON log line to all candidate log paths that are writable."""
    line = json.dumps(record, ensure_ascii=False) + "\n"
    written: set[str] = set()
    for path in _LOG_CANDIDATES:
        key = str(path.resolve()) if path.is_absolute() else str(path)
        if key in written:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            written.add(key)
        except Exception as exc:  # noqa: BLE001
            _std_logger.debug("Could not write backend log to %s: %s", path, exc)


def _log_event(
    event: str,
    *,
    request_id: str,
    endpoint: str,
    method: str,
    payload: Optional[dict[str, Any]] = None,
    response: Optional[dict[str, Any]] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[float] = None,
    **extra: Any,
) -> None:
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "request_id": request_id,
        "endpoint": endpoint,
        "method": method,
    }
    if payload is not None:
        record["payload"] = payload
    if status_code is not None:
        record["status_code"] = status_code
    if duration_ms is not None:
        record["duration_ms"] = round(duration_ms, 3)
    if response is not None:
        record["response"] = response
    record.update(extra)
    _write_log(record)
    _std_logger.info("[%s] %s %s req=%s", event, method, endpoint, request_id)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CheckAvailabilityRequest(BaseModel):
    date: str = Field(..., description="ISO-8601 date string e.g. '2026-09-10'")
    request_id: str = Field(..., description="Caller-supplied idempotency / correlation ID")
    delay: Optional[float] = Field(None, description="Override artificial delay in seconds (for tests)")


class CheckAvailabilityResponse(BaseModel):
    available_slots: list[str]
    request_id: str


class BookRequest(BaseModel):
    date: str = Field(..., description="ISO-8601 date string e.g. '2026-09-10'")
    slot: str = Field(..., description="Time slot string e.g. '14:00'")
    request_id: str = Field(..., description="Caller-supplied idempotency / correlation ID")


class BookResponse(BaseModel):
    status: str
    confirmation: str
    booking_id: str
    date: str
    slot: str
    request_id: str


class HealthResponse(BaseModel):
    status: str
    timestamp: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Snapback Booking Backend",
    description="FastAPI backend providing appointment availability and booking endpoints.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Middleware — log raw timing (optional enhancement)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def _request_timer(request: Request, call_next):  # type: ignore[type-arg]
    request.state.t0 = time.perf_counter()
    response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    """Simple liveness check."""
    return HealthResponse(status="ok", timestamp=datetime.now(timezone.utc).isoformat())


@app.post("/check-availability", response_model=CheckAvailabilityResponse, tags=["booking"])
async def check_availability(body: CheckAvailabilityRequest) -> CheckAvailabilityResponse:
    """
    Return available appointment slots for a given date.
    Applies an artificial delay (default 4 s) to simulate a real availability lookup.
    """
    t0 = time.perf_counter()
    endpoint = "/check-availability"
    method = "POST"
    payload = body.model_dump()

    _log_event(
        "request_in",
        request_id=body.request_id,
        endpoint=endpoint,
        method=method,
        payload=payload,
    )

    delay = body.delay if body.delay is not None else DEFAULT_DELAY
    await asyncio.sleep(delay)

    response_data = CheckAvailabilityResponse(
        available_slots=_AVAILABLE_SLOTS,
        request_id=body.request_id,
    )

    duration_ms = (time.perf_counter() - t0) * 1000.0
    _log_event(
        "request_out",
        request_id=body.request_id,
        endpoint=endpoint,
        method=method,
        status_code=200,
        duration_ms=duration_ms,
        response=response_data.model_dump(),
    )

    return response_data


@app.post("/book", response_model=BookResponse, tags=["booking"])
async def book(body: BookRequest) -> BookResponse:
    """Confirm an appointment booking for a given date and slot."""
    t0 = time.perf_counter()
    endpoint = "/book"
    method = "POST"
    payload = body.model_dump()

    _log_event(
        "request_in",
        request_id=body.request_id,
        endpoint=endpoint,
        method=method,
        payload=payload,
    )

    booking_id = f"BKG-{uuid.uuid4().hex[:8].upper()}"
    response_data = BookResponse(
        status="confirmed",
        confirmation=f"Booking confirmed for {body.date} at {body.slot}.",
        booking_id=booking_id,
        date=body.date,
        slot=body.slot,
        request_id=body.request_id,
    )

    duration_ms = (time.perf_counter() - t0) * 1000.0
    _log_event(
        "request_out",
        request_id=body.request_id,
        endpoint=endpoint,
        method=method,
        status_code=200,
        duration_ms=duration_ms,
        response=response_data.model_dump(),
    )

    return response_data


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("BACKEND_PORT", "8000"))
    uvicorn.run("backend:app", host="0.0.0.0", port=port, reload=True)
