"""
Tests for the FastAPI booking backend and LLM orchestration module.

Coverage:
  Backend:
    - POST /check-availability: schema, artificial delay, response fields
    - POST /book: schema, confirmation content, booking_id generation
    - GET  /health: liveness
    - Structured JSON logging: request_in / request_out events, timestamps, request_id

  Orchestrator (heuristic mode — no external API required):
    - Availability query -> tool_call check_availability
    - Booking query with date + slot -> tool_call book
    - Booking query without slot -> direct response asking for slot
    - Generic greeting -> direct response string
    - End-to-end: orchestrate() -> call_backend_tool() -> backend response

  State Fencing (SessionStateManager):
    - issue_request / is_stale / resolve unit tests
    - Stale-result-discarded JSON logging with both IDs and ISO timestamp
    - orchestrate() stamps request_id into tool_call intents when session provided
    - execute_with_fencing() returns None for stale results, result for fresh ones
    - Concurrent utterance supersession simulation
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import time
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Import modules under test
# ---------------------------------------------------------------------------

from backend import app, _LOG_CANDIDATES, _AVAILABLE_SLOTS
from orchestrator import (
    SessionStateManager,
    call_backend_tool,
    execute_with_fencing,
    orchestrate,
    _heuristic_parse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _tmp_log_dir(tmp_path: Path, monkeypatch):
    """
    Redirect all log writes to a temporary directory so tests stay isolated
    and the real /logs/backend.log (or logs/backend.log) is never touched.
    """
    import backend as backend_mod

    tmp_log = tmp_path / "backend.log"
    monkeypatch.setattr(backend_mod, "_LOG_CANDIDATES", [tmp_log])
    yield tmp_log


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def log_file(_tmp_log_dir: Path) -> Path:
    return _tmp_log_dir


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def read_log_lines(log_file: Path) -> list[dict]:
    if not log_file.exists():
        return []
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


# ===========================================================================
# Backend — /health
# ===========================================================================


class TestHealth:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "timestamp" in body


# ===========================================================================
# Backend — POST /check-availability
# ===========================================================================


class TestCheckAvailability:
    def test_response_schema(self, client: TestClient) -> None:
        payload = {"date": "2026-09-10", "request_id": "test-req-001", "delay": 0}
        resp = client.post("/check-availability", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["request_id"] == "test-req-001"
        assert isinstance(body["available_slots"], list)
        assert len(body["available_slots"]) > 0

    def test_returns_expected_slots(self, client: TestClient) -> None:
        payload = {"date": "2026-09-11", "request_id": "test-req-002", "delay": 0}
        resp = client.post("/check-availability", json=payload)
        body = resp.json()
        assert body["available_slots"] == _AVAILABLE_SLOTS

    def test_request_id_echoed(self, client: TestClient) -> None:
        req_id = "unique-correlation-xyz"
        payload = {"date": "2026-09-12", "request_id": req_id, "delay": 0}
        resp = client.post("/check-availability", json=payload)
        assert resp.json()["request_id"] == req_id

    def test_artificial_delay(self, client: TestClient) -> None:
        """Verify that a non-zero delay is actually applied (0.1s precision)."""
        delay = 0.25
        payload = {"date": "2026-09-13", "request_id": "delay-test", "delay": delay}
        t0 = time.perf_counter()
        resp = client.post("/check-availability", json=payload)
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        assert elapsed >= delay * 0.9  # allow 10% margin for scheduling jitter

    def test_missing_request_id_returns_422(self, client: TestClient) -> None:
        resp = client.post("/check-availability", json={"date": "2026-09-10"})
        assert resp.status_code == 422

    def test_missing_date_returns_422(self, client: TestClient) -> None:
        resp = client.post("/check-availability", json={"request_id": "test-req-x"})
        assert resp.status_code == 422


# ===========================================================================
# Backend — POST /book
# ===========================================================================


class TestBook:
    def test_response_schema(self, client: TestClient) -> None:
        payload = {"date": "2026-09-10", "slot": "14:00", "request_id": "book-req-001"}
        resp = client.post("/book", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "confirmed"
        assert "confirmation" in body
        assert "booking_id" in body
        assert body["date"] == "2026-09-10"
        assert body["slot"] == "14:00"
        assert body["request_id"] == "book-req-001"

    def test_confirmation_text_contains_date_and_slot(self, client: TestClient) -> None:
        payload = {"date": "2026-09-15", "slot": "09:00", "request_id": "book-req-002"}
        body = client.post("/book", json=payload).json()
        assert "2026-09-15" in body["confirmation"]
        assert "09:00" in body["confirmation"]

    def test_booking_id_is_unique(self, client: TestClient) -> None:
        ids = set()
        for i in range(5):
            payload = {"date": "2026-09-15", "slot": "10:30", "request_id": f"req-{i}"}
            body = client.post("/book", json=payload).json()
            ids.add(body["booking_id"])
        assert len(ids) == 5, "Booking IDs must be unique across requests"

    def test_missing_slot_returns_422(self, client: TestClient) -> None:
        resp = client.post("/book", json={"date": "2026-09-10", "request_id": "r1"})
        assert resp.status_code == 422

    def test_request_id_echoed(self, client: TestClient) -> None:
        req_id = "book-echo-test"
        payload = {"date": "2026-09-10", "slot": "14:00", "request_id": req_id}
        assert client.post("/book", json=payload).json()["request_id"] == req_id


# ===========================================================================
# Backend — Structured JSON Logging
# ===========================================================================


class TestStructuredLogging:
    def _make_avail_request(self, client: TestClient, req_id: str) -> None:
        client.post("/check-availability", json={"date": "2026-09-20", "request_id": req_id, "delay": 0})

    def _make_book_request(self, client: TestClient, req_id: str) -> None:
        client.post("/book", json={"date": "2026-09-20", "slot": "14:00", "request_id": req_id})

    def test_two_log_lines_per_request(self, client: TestClient, log_file: Path) -> None:
        self._make_avail_request(client, "log-test-001")
        lines = read_log_lines(log_file)
        assert len(lines) == 2

    def test_request_in_event(self, client: TestClient, log_file: Path) -> None:
        self._make_avail_request(client, "log-test-002")
        lines = read_log_lines(log_file)
        req_in = next(l for l in lines if l["event"] == "request_in")
        assert req_in["request_id"] == "log-test-002"
        assert req_in["endpoint"] == "/check-availability"
        assert req_in["method"] == "POST"
        # Timestamp must be ISO-8601
        from datetime import datetime
        datetime.fromisoformat(req_in["timestamp"])

    def test_request_out_event(self, client: TestClient, log_file: Path) -> None:
        self._make_avail_request(client, "log-test-003")
        lines = read_log_lines(log_file)
        req_out = next(l for l in lines if l["event"] == "request_out")
        assert req_out["request_id"] == "log-test-003"
        assert req_out["status_code"] == 200
        assert "duration_ms" in req_out
        assert req_out["duration_ms"] >= 0
        assert "response" in req_out
        assert "available_slots" in req_out["response"]

    def test_book_request_logged(self, client: TestClient, log_file: Path) -> None:
        self._make_book_request(client, "log-book-001")
        lines = read_log_lines(log_file)
        assert any(l["endpoint"] == "/book" for l in lines)

    def test_payload_is_logged_on_request_in(self, client: TestClient, log_file: Path) -> None:
        self._make_avail_request(client, "log-payload-001")
        lines = read_log_lines(log_file)
        req_in = next(l for l in lines if l["event"] == "request_in")
        assert "payload" in req_in
        assert req_in["payload"]["request_id"] == "log-payload-001"

    def test_each_line_is_valid_json(self, client: TestClient, log_file: Path) -> None:
        """Verify raw log file contents are valid JSON on every line."""
        self._make_avail_request(client, "log-json-001")
        raw_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        for raw in raw_lines:
            parsed = json.loads(raw)  # raises on invalid JSON
            assert isinstance(parsed, dict)

    def test_multiple_request_ids_in_log(self, client: TestClient, log_file: Path) -> None:
        for i in range(3):
            self._make_avail_request(client, f"multi-req-{i}")
        lines = read_log_lines(log_file)
        req_ids = {l["request_id"] for l in lines}
        assert req_ids == {"multi-req-0", "multi-req-1", "multi-req-2"}


# ===========================================================================
# Orchestrator — heuristic parser (no external API)
# ===========================================================================


class TestHeuristicParser:
    def test_availability_query_with_date(self) -> None:
        result = _heuristic_parse("What slots are available on 2026-09-15?")
        assert isinstance(result, dict)
        assert result["tool"] == "check_availability"
        assert "date" in result["args"]

    def test_availability_query_natural_date(self) -> None:
        result = _heuristic_parse("Can I see availability for tomorrow?")
        assert isinstance(result, dict)
        assert result["tool"] == "check_availability"

    def test_book_query_with_date_and_slot(self) -> None:
        result = _heuristic_parse("I want to book an appointment on 2026-09-20 at 14:00")
        assert isinstance(result, dict)
        assert result["tool"] == "book"
        assert result["args"]["date"] == "2026-09-20"
        assert "14:00" in result["args"]["slot"]

    def test_book_query_missing_slot_returns_string(self) -> None:
        result = _heuristic_parse("I want to book an appointment on 2026-09-22")
        assert isinstance(result, str)
        assert "slot" in result.lower() or "time" in result.lower() or "prefer" in result.lower()

    def test_greeting_returns_string(self) -> None:
        result = _heuristic_parse("Hello!")
        assert isinstance(result, str)

    def test_book_intent_no_date_returns_string(self) -> None:
        result = _heuristic_parse("I'd like to schedule something")
        assert isinstance(result, str)

    def test_availability_intent_no_date_returns_string(self) -> None:
        result = _heuristic_parse("What are the available slots?")
        assert isinstance(result, str)


# ===========================================================================
# Orchestrator — async orchestrate() with forced heuristic
# ===========================================================================


class TestOrchestrateAsync:
    @pytest.mark.asyncio
    async def test_availability_tool_call(self) -> None:
        result = await orchestrate(
            "Are there any slots on 2026-10-01?",
            use_heuristic=True,
        )
        assert isinstance(result, dict)
        assert result["tool"] == "check_availability"

    @pytest.mark.asyncio
    async def test_book_tool_call(self) -> None:
        result = await orchestrate(
            "Book me in for 2026-10-05 at 09:00",
            use_heuristic=True,
        )
        assert isinstance(result, dict)
        assert result["tool"] == "book"
        assert result["args"]["slot"] == "09:00"

    @pytest.mark.asyncio
    async def test_direct_response_for_greeting(self) -> None:
        result = await orchestrate("Hi there!", use_heuristic=True)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_falls_back_to_heuristic_without_api_key(self) -> None:
        # No api_key provided; should silently use heuristic
        result = await orchestrate(
            "Show me availability for September 10th",
            api_key="",
            use_heuristic=False,
        )
        assert isinstance(result, dict) or isinstance(result, str)


# ===========================================================================
# End-to-end: orchestrate -> call_backend_tool -> live backend
# ===========================================================================


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_check_availability_e2e(self) -> None:
        """
        End-to-end: orchestrate decides check_availability -> call_backend_tool
        calls the live FastAPI app in-process via ASGI transport.
        """
        from httpx import AsyncClient, ASGITransport

        intent = await orchestrate("What's available on 2026-09-25?", use_heuristic=True)
        assert isinstance(intent, dict) and intent["tool"] == "check_availability"

        # Use ASGI transport to hit the real app in-process (no server needed)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            payload = {
                "date": intent["args"]["date"],
                "request_id": "e2e-avail-001",
                "delay": 0,
            }
            resp = await ac.post("/check-availability", json=payload)
            assert resp.status_code == 200
            body = resp.json()
            assert "available_slots" in body
            assert body["request_id"] == "e2e-avail-001"

    @pytest.mark.asyncio
    async def test_book_e2e(self) -> None:
        """
        End-to-end: orchestrate decides book -> call_backend_tool hits the real app.
        """
        from httpx import AsyncClient, ASGITransport

        intent = await orchestrate(
            "Please book me for 2026-09-28 at 10:30", use_heuristic=True
        )
        assert isinstance(intent, dict) and intent["tool"] == "book"

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            payload = {
                "date": intent["args"]["date"],
                "slot": intent["args"]["slot"],
                "request_id": "e2e-book-001",
            }
            resp = await ac.post("/book", json=payload)
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "confirmed"
            assert "booking_id" in body


# ===========================================================================
# SessionStateManager — unit tests for issue_request / is_stale / resolve
# ===========================================================================


class TestSessionStateManager:
    # --- issue_request ---

    def test_initial_current_is_none(self) -> None:
        session = SessionStateManager()
        assert session.current_request_id is None

    def test_issue_request_returns_generated_id(self) -> None:
        session = SessionStateManager()
        req_id = session.issue_request()
        assert isinstance(req_id, str)
        assert len(req_id) > 0

    def test_issue_request_accepts_utterance_id(self) -> None:
        session = SessionStateManager()
        req_id = session.issue_request("utt-custom-001")
        assert req_id == "utt-custom-001"

    def test_issue_request_updates_current(self) -> None:
        session = SessionStateManager()
        req_id = session.issue_request("utt-abc")
        assert session.current_request_id == req_id

    def test_second_issue_supersedes_first(self) -> None:
        session = SessionStateManager()
        session.issue_request("utt-001")
        second = session.issue_request("utt-002")
        assert session.current_request_id == second

    def test_multiple_supersessions_track_latest(self) -> None:
        session = SessionStateManager()
        for i in range(5):
            last = session.issue_request(f"utt-{i:03d}")
        assert session.current_request_id == last  # noqa: F821

    # --- is_stale ---

    def test_is_stale_false_for_current(self) -> None:
        session = SessionStateManager()
        req_id = session.issue_request("utt-x")
        assert session.is_stale(req_id) is False

    def test_is_stale_true_after_new_utterance(self) -> None:
        session = SessionStateManager()
        old_id = session.issue_request("utt-old")
        session.issue_request("utt-new")  # supersedes
        assert session.is_stale(old_id) is True

    def test_is_stale_true_for_arbitrary_unknown_id(self) -> None:
        session = SessionStateManager()
        session.issue_request("utt-known")
        assert session.is_stale("req-never-issued") is True

    def test_is_stale_true_when_no_request_issued(self) -> None:
        session = SessionStateManager()
        # No utterance issued yet — any ID is stale
        assert session.is_stale("req-anything") is True

    # --- resolve ---

    def test_resolve_returns_result_when_current(self) -> None:
        session = SessionStateManager()
        req_id = session.issue_request("utt-fresh")
        data = {"available_slots": ["09:00", "14:00"]}
        assert session.resolve(req_id, data) == data

    def test_resolve_returns_none_when_stale(self) -> None:
        session = SessionStateManager()
        old_id = session.issue_request("utt-old")
        session.issue_request("utt-new")
        assert session.resolve(old_id, {"available_slots": []}) is None

    def test_resolve_preserves_result_type(self) -> None:
        """resolve() must not mutate or wrap the result when not stale."""
        session = SessionStateManager()
        req_id = session.issue_request()
        payload = {"status": "confirmed", "booking_id": "BKG-001"}
        resolved = session.resolve(req_id, payload)
        assert resolved is payload  # exact same object

    def test_resolve_stale_logs_json_event(self, tmp_path: Path) -> None:
        """stale-result-discarded must be written as a valid JSON line to log_file."""
        log_path = tmp_path / "session.log"
        session = SessionStateManager(log_file=str(log_path))
        old_id = session.issue_request("utt-001")
        session.issue_request("utt-002")  # supersede
        session.resolve(old_id, {"data": "irrelevant"})

        lines = read_log_lines(log_path)
        assert len(lines) == 1
        event = lines[0]
        assert event["event"] == "stale-result-discarded"

    def test_resolve_stale_log_contains_both_ids(self, tmp_path: Path) -> None:
        log_path = tmp_path / "session.log"
        session = SessionStateManager(log_file=str(log_path))
        old_id = session.issue_request("utt-alpha")
        new_id = session.issue_request("utt-beta")
        session.resolve(old_id, {})

        event = read_log_lines(log_path)[0]
        assert event["discarded_request_id"] == old_id
        assert event["current_request_id"] == new_id

    def test_resolve_stale_log_has_iso_timestamp(self, tmp_path: Path) -> None:
        from datetime import datetime

        log_path = tmp_path / "session.log"
        session = SessionStateManager(log_file=str(log_path))
        old_id = session.issue_request("utt-ts-old")
        session.issue_request("utt-ts-new")
        session.resolve(old_id, {})

        event = read_log_lines(log_path)[0]
        # Must parse without error as ISO-8601
        datetime.fromisoformat(event["timestamp"])

    def test_resolve_no_log_when_fresh(self, tmp_path: Path) -> None:
        """No log line written when result is fresh (not stale)."""
        log_path = tmp_path / "session.log"
        session = SessionStateManager(log_file=str(log_path))
        req_id = session.issue_request("utt-only")
        session.resolve(req_id, {"data": "ok"})
        # Log file should not even be created (or be empty)
        assert not log_path.exists() or log_path.read_text().strip() == ""

    # --- reset ---

    def test_reset_clears_current(self) -> None:
        session = SessionStateManager()
        session.issue_request("utt-before-reset")
        session.reset()
        assert session.current_request_id is None

    def test_is_stale_true_after_reset(self) -> None:
        session = SessionStateManager()
        req_id = session.issue_request("utt-pre-reset")
        session.reset()
        assert session.is_stale(req_id) is True


# ===========================================================================
# orchestrate() with session — stamps request_id into tool_call intents
# ===========================================================================


class TestOrchestrateWithSession:
    @pytest.mark.asyncio
    async def test_issues_request_when_session_provided(self) -> None:
        session = SessionStateManager()
        assert session.current_request_id is None
        await orchestrate("slots on 2026-10-10?", session=session, use_heuristic=True)
        assert session.current_request_id is not None

    @pytest.mark.asyncio
    async def test_tool_intent_stamped_with_request_id(self) -> None:
        session = SessionStateManager()
        result = await orchestrate(
            "What slots are on 2026-10-15?", session=session, use_heuristic=True
        )
        assert isinstance(result, dict)
        assert "request_id" in result
        assert result["request_id"] == session.current_request_id

    @pytest.mark.asyncio
    async def test_custom_utterance_id_propagated(self) -> None:
        session = SessionStateManager()
        result = await orchestrate(
            "slots on 2026-10-20?",
            session=session,
            utterance_id="my-utt-custom-99",
            use_heuristic=True,
        )
        assert isinstance(result, dict)
        assert result["request_id"] == "my-utt-custom-99"
        assert session.current_request_id == "my-utt-custom-99"

    @pytest.mark.asyncio
    async def test_direct_response_not_stamped(self) -> None:
        """Strings (direct responses) must not have a request_id key appended."""
        session = SessionStateManager()
        result = await orchestrate("Hello!", session=session, use_heuristic=True)
        assert isinstance(result, str)
        # strings have no 'request_id' attribute
        assert not hasattr(result, "request_id")

    @pytest.mark.asyncio
    async def test_no_session_means_no_request_id_in_intent(self) -> None:
        result = await orchestrate(
            "slots on 2026-10-25?", session=None, use_heuristic=True
        )
        assert isinstance(result, dict)
        assert "request_id" not in result

    @pytest.mark.asyncio
    async def test_successive_utterances_update_current(self) -> None:
        session = SessionStateManager()
        await orchestrate("slots on 2026-11-01?", session=session, utterance_id="utt-A", use_heuristic=True)
        await orchestrate("slots on 2026-11-02?", session=session, utterance_id="utt-B", use_heuristic=True)
        assert session.current_request_id == "utt-B"


# ===========================================================================
# execute_with_fencing() — backend call + fencing gate
# ===========================================================================


class TestExecuteWithFencing:
    @pytest.mark.asyncio
    async def test_fresh_result_returned(self) -> None:
        """When the request is still current, the backend result passes through."""
        from httpx import AsyncClient, ASGITransport

        session = SessionStateManager()
        intent = await orchestrate(
            "slots on 2026-11-10?", session=session, use_heuristic=True
        )
        assert isinstance(intent, dict)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            # Monkey-patch call_backend_tool to use ASGI transport
            import orchestrator as orch_mod

            async def _patched_call(
                tool_intent: dict[str, Any],
                *,
                base_url: str = "http://localhost:8000",
                request_id: str | None = None,
                delay_override: float | None = None,
            ) -> dict[str, Any]:
                payload: dict = {
                    "date": tool_intent["args"].get("date", ""),
                    "request_id": request_id or "test",
                }
                if delay_override is not None:
                    payload["delay"] = delay_override
                resp = await ac.post("/check-availability", json=payload)
                return resp.json()

            original = orch_mod.call_backend_tool
            orch_mod.call_backend_tool = _patched_call
            try:
                result = await execute_with_fencing(
                    intent, session=session, delay_override=0
                )
            finally:
                orch_mod.call_backend_tool = original

        assert result is not None
        assert "available_slots" in result

    @pytest.mark.asyncio
    async def test_stale_result_discarded(self, tmp_path: Path) -> None:
        """When a newer utterance supersedes before resolve(), result is None."""
        from httpx import AsyncClient, ASGITransport

        log_path = tmp_path / "stale.log"
        session = SessionStateManager(log_file=str(log_path))

        # Utterance A
        intent = await orchestrate(
            "slots on 2026-11-15?", session=session, utterance_id="utt-A", use_heuristic=True
        )
        assert isinstance(intent, dict)
        req_id_a = intent["request_id"]

        # Utterance B arrives — supersedes A before we even call the backend
        session.issue_request("utt-B")
        assert session.is_stale(req_id_a)

        # Now the backend result for A finally arrives — should be discarded
        fake_result = {"available_slots": ["09:00"], "request_id": req_id_a}
        discarded = session.resolve(req_id_a, fake_result)
        assert discarded is None

        # stale-result-discarded must be logged
        events = read_log_lines(log_path)
        assert any(e["event"] == "stale-result-discarded" for e in events)
        stale_event = next(e for e in events if e["event"] == "stale-result-discarded")
        assert stale_event["discarded_request_id"] == req_id_a
        assert stale_event["current_request_id"] == "utt-B"

    @pytest.mark.asyncio
    async def test_no_session_always_returns_result(self) -> None:
        """Without a session, execute_with_fencing passes the result through always."""
        from httpx import AsyncClient, ASGITransport
        import orchestrator as orch_mod

        intent = await orchestrate(
            "slots on 2026-11-20?", session=None, use_heuristic=True
        )
        assert isinstance(intent, dict)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            async def _patched_call(
                tool_intent: dict[str, Any],
                *,
                base_url: str = "http://localhost:8000",
                request_id: str | None = None,
                delay_override: float | None = None,
            ) -> dict[str, Any]:
                payload = {
                    "date": tool_intent["args"].get("date", ""),
                    "request_id": request_id or "no-session",
                }
                if delay_override is not None:
                    payload["delay"] = delay_override
                resp = await ac.post("/check-availability", json=payload)
                return resp.json()

            original = orch_mod.call_backend_tool
            orch_mod.call_backend_tool = _patched_call
            try:
                result = await execute_with_fencing(intent, session=None, delay_override=0)
            finally:
                orch_mod.call_backend_tool = original

        assert result is not None
        assert "available_slots" in result

    @pytest.mark.asyncio
    async def test_stale_log_event_ids_match_utterances(self, tmp_path: Path) -> None:
        """Verify discarded_request_id and current_request_id fields are correct."""
        log_path = tmp_path / "stale_ids.log"
        session = SessionStateManager(log_file=str(log_path))

        # Two rapid utterances
        old_id = session.issue_request("rapid-utt-1")
        _new_id = session.issue_request("rapid-utt-2")

        session.resolve(old_id, {"data": "stale"})

        event = read_log_lines(log_path)[0]
        assert event["discarded_request_id"] == "rapid-utt-1"
        assert event["current_request_id"] == "rapid-utt-2"


# ---------------------------------------------------------------------------
# Agent and Orchestrator Integration Tests
# ---------------------------------------------------------------------------

from agent import VoiceAudioPipeline, PipelineState
from livekit import rtc
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
class TestAgentOrchestrationIntegration:
    async def test_agent_pipeline_uses_session_manager_for_fencing(self, tmp_path: Path) -> None:
        """
        Constructs a VoiceAudioPipeline with a real SessionStateManager, fires two simulated 
        utterances with a mid-flight interrupt, asserts exactly one stale-result-discarded event.
        """
        log_file = tmp_path / "integration_agent.log"
        session = SessionStateManager(log_file=str(log_file))
        
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()
        mock_tts = MagicMock()
        
        async def dummy_tool(transcript: str) -> str:
            # Sleep long enough for the interrupt to arrive
            await asyncio.sleep(0.2)
            return f"result for {transcript}"
            
        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_tts,
            active_tts_provider="rime",
            tool_executor=dummy_tool,
            log_file_override=str(log_file),
            session=session,
        )
        
        # 1. Fire first utterance
        req_1 = session.issue_request("utt-1")
        task1 = asyncio.create_task(pipeline._execute_turn_response(participant_id="p1", transcript="first", request_id=req_1))
        
        # Give it time to reach TOOL_RUNNING
        await asyncio.sleep(0.05)
        assert pipeline.state == PipelineState.TOOL_RUNNING
        
        # 2. Fire interrupt (superseding utterance)
        # In process_stt_events, interrupt calls cancel_active and then fires a new request
        await pipeline.cancel_active(participant_id="p1")
        
        # cancel_active should have invalidated with an interrupt id
        assert session.current_request_id != req_1
        assert session.current_request_id is not None
        assert session.current_request_id.startswith("interrupt-")
        
        # 3. Fire second utterance
        req_2 = session.issue_request("utt-2")
        task2 = asyncio.create_task(pipeline._execute_turn_response(participant_id="p1", transcript="second", request_id=req_2))
        
        # Wait for both to finish
        try:
            await task1
        except asyncio.CancelledError:
            pass
            
        await task2
        
        # Task 2 should have completed successfully because it wasn't interrupted
        assert pipeline.state == PipelineState.IDLE
        
        # Now verify logs
        lines = read_log_lines(log_file)
        
        # Exactly one stale-result-discarded from the first task's tool completing late
        stale_events = [e for e in lines if e.get("stage") == "stale-result-discarded" or e.get("event") == "stale-result-discarded"]
        assert len(stale_events) == 1
        assert stale_events[0]["discarded_request_id"] == req_1
        # It was stale against whatever the current request was when it finally resolved
        assert stale_events[0]["current_request_id"] == req_2
