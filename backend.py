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
import psutil
import subprocess
import sys
import time
from typing import Any, Optional
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from livekit.api import AccessToken, VideoGrants
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger("snapback-backend")

_agent_process: Optional[subprocess.Popen] = None


def is_agent_process_running() -> bool:
    """Check if any agent.py or run_agent.py process is active across the system."""
    global _agent_process
    if _agent_process is not None and _agent_process.poll() is None:
        return True
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "agent.py" in cmdline or "run_agent.py" in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


def _ensure_agent_running() -> bool:
    """Ensure run_agent.py is running in the background to serve incoming LiveKit calls."""
    global _agent_process
    if os.getenv("SNAPBACK_AUTO_SPAWN_AGENT", "true").lower() == "false":
        return False
    if is_agent_process_running():
        return True
    try:
        run_agent_script = Path(__file__).parent / "run_agent.py"
        if run_agent_script.exists():
            _agent_process = subprocess.Popen(
                [sys.executable, str(run_agent_script)],
                cwd=str(Path(__file__).parent.resolve()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Auto-spawned agent supervisor process (pid=%s)", _agent_process.pid)
            return True
    except Exception as exc:
        logger.warning("Could not auto-spawn run_agent.py: %s", exc)
    return False

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

# Candidate agent timeline log paths (the structured JSONL the agent writes)
AGENT_LOG_OVERRIDE: str = os.getenv("LOG_FILE_PATH", "logs/agent.log")
_AGENT_LOG_CANDIDATES: list[Path] = [
    Path(AGENT_LOG_OVERRIDE),
    Path("logs/agent.log"),
    Path("/logs/agent.log"),
]


def _agent_log_candidates() -> list[Path]:
    """Deduplicate agent log candidate paths by resolved location."""
    seen: set[str] = set()
    out: list[Path] = []
    for raw in _AGENT_LOG_CANDIDATES:
        try:
            key = str(raw.resolve())
        except Exception:
            key = str(raw)
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out

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


class TokenRequest(BaseModel):
    room: str = Field(..., description="LiveKit room name")
    identity: str = Field(..., description="Participant identity")


class TokenResponse(BaseModel):
    token: str
    url: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Snapback Booking Backend",
    description="FastAPI backend providing appointment availability, booking, and LiveKit token endpoints.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.post("/token", response_model=TokenResponse, tags=["livekit"])
async def get_token(body: TokenRequest) -> TokenResponse:
    """Issue a LiveKit access token for the given room and identity."""
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    url = os.getenv("LIVEKIT_URL", "wss://your-project.livekit.cloud")

    if not api_key or not api_secret:
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured")

    grant = VideoGrants(
        room_join=True,
        room=body.room,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        AccessToken(api_key=api_key, api_secret=api_secret)
        .with_identity(body.identity)
        .with_grants(grant)
        .to_jwt()
    )

    _log_event(
        "token_issued",
        request_id=f"tok-{uuid.uuid4().hex[:8]}",
        endpoint="/token",
        method="POST",
        payload={"room": body.room, "identity": body.identity},
    )

    # Ensure background voice agent worker is running and attached to room
    _ensure_agent_running()

    return TokenResponse(token=token, url=url)


@app.get("/agent-status", tags=["livekit"])
async def agent_status() -> dict[str, Any]:
    """Check whether the agent supervisor process is actively running."""
    is_running = is_agent_process_running()
    return {
        "agent_running": is_running,
        "pid": _agent_process.pid if _agent_process and _agent_process.poll() is None else None,
    }


@app.post("/save-recording", tags=["recording"])
async def save_recording(request: Request) -> JSONResponse:
    """Save an uploaded screen/audio recording (.webm) to project root and artifacts directory."""
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty recording payload")
    
    out_paths = [
        Path("live_interrupt_demo.webm"),
        Path("logs/live_interrupt_demo.webm"),
    ]
    for p in out_paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "wb") as f:
                f.write(body)
        except Exception as e:
            logger.warning(f"Could not write recording to {p}: {e}")
            
    return JSONResponse({"status": "saved", "bytes": len(body), "filename": "live_interrupt_demo.webm"})


@app.get("/events", tags=["realtime"])
async def stream_agent_events(tail: int = 300) -> StreamingResponse:
    """
    Server-Sent Events stream of the agent's structured timeline log.

    Emits each new JSON line from logs/agent.log (and candidate mirrors) as an
    SSE ``data:`` frame. On connect, replays the most recent *tail* lines so the
    client can rebuild history. Out-of-order / duplicate events are filtered
    server-side using the per-run monotonic ``seq`` field.

    Event payload schema (one JSON object per line):
        {timestamp, seq, run_id, stage, state, previous_state,
         active_tts_provider, request_id, latency_ms}
    """

    async def event_generator() -> Any:
        # run_id -> highest seq already streamed (out-of-order/duplicate filter)
        seen_seq: dict[str, int] = {}
        # exact-line hash set for events without run_id/seq
        seen_flat: set[str] = set()
        # path -> byte offset we have consumed up to
        positions: dict[Path, int] = {}
        emitted = 0
        last_activity = time.monotonic()

        while True:
            produced = False
            for path in _agent_log_candidates():
                if not path.exists():
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue

                if path not in positions:
                    # First sighting: replay the most recent *tail* complete
                    # lines of history, then continue live below. We read a
                    # generous window (per-line budget covers long transcript
                    # text) and keep exactly the last `tail` lines so a line is
                    # never sliced in half. tail=0 replays nothing.
                    size = stat.st_size
                    if size <= 0:
                        positions[path] = 0
                        continue
                    window_bytes = min(size, max(tail, emitted) * 4096 + 8192)
                    start = max(0, size - window_bytes)
                    if start > 0:
                        # Align the window start to a line boundary.
                        probe_start = max(0, start - 65536)
                        try:
                            with open(path, "rb") as fh:
                                fh.seek(probe_start)
                                probe = fh.read(start - probe_start)
                            nl = probe.rfind(b"\n")
                            if nl != -1:
                                start = probe_start + nl + 1
                        except OSError:
                            pass
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(start)
                            chunk = fh.read()
                            new_pos = fh.tell()
                    except OSError:
                        continue
                    positions[path] = new_pos

                    for raw in (chunk.splitlines()[-tail:] if tail > 0 else []):
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            ev = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        run_id = ev.get("run_id")
                        seq = ev.get("seq")
                        if isinstance(run_id, str) and isinstance(seq, int):
                            last = seen_seq.get(run_id, -1)
                            if seq <= last:
                                continue
                            seen_seq[run_id] = seq
                        else:
                            digest = raw
                            if digest in seen_flat:
                                continue
                            seen_flat.add(digest)

                        yield f"data: {raw}\n\n"
                        emitted += 1
                        produced = True
                    continue

                pos = positions[path]
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        new_pos = fh.tell()
                except OSError:
                    continue
                if new_pos <= pos:
                    continue
                positions[path] = new_pos

                for raw in chunk.splitlines():
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    run_id = ev.get("run_id")
                    seq = ev.get("seq")
                    if isinstance(run_id, str) and isinstance(seq, int):
                        last = seen_seq.get(run_id, -1)
                        if seq <= last:
                            continue
                        seen_seq[run_id] = seq
                    else:
                        digest = raw
                        if digest in seen_flat:
                            continue
                        seen_flat.add(digest)

                    yield f"data: {raw}\n\n"
                    emitted += 1
                    produced = True

            if produced:
                last_activity = time.monotonic()
            elif time.monotonic() - last_activity > 0.5:
                yield ": ping\n\n"
                last_activity = time.monotonic()
            await asyncio.sleep(0.02)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("BACKEND_PORT", "8000"))
    uvicorn.run("backend:app", host="0.0.0.0", port=port, reload=True)
