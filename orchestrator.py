"""
LLM Orchestration Module — Booking Assistant

Takes a transcript string and returns either:
  - A tool_call intent dict: {"tool": "<name>", "args": { ... }, "request_id": "<id>"}
  - A direct response string: "<conversational reply>"

Supported tools:
  - check_availability(date: str)
  - book(date: str, slot: str)

When OPENAI_API_KEY is available, uses OpenAI function-calling.
Falls back to a deterministic heuristic parser so the module works
without any external API (useful for unit tests and local development).

State fencing:
  Every tool call is tagged with a unique request_id tied to the user utterance
  that triggered it. SessionStateManager maintains a "current_request_id" pointer
  updated on each new utterance. execute_with_fencing() gates tool results through
  SessionStateManager.resolve() — stale results are discarded and logged.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Optional, cast

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("snapback-orchestrator")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(_h)


# ---------------------------------------------------------------------------
# Session State Manager — request-ID based state fencing
# ---------------------------------------------------------------------------


class SessionStateManager:
    """
    In-memory session state manager for request-ID based state fencing.

    Tracks the "current" request_id tied to the most recent user utterance.
    Any tool result carrying an older request_id is considered stale and is
    discarded before it can reach the LLM for final response generation.

    All methods are synchronous for unit-testability. Thread safety is
    provided by a threading.Lock so the manager works safely in async
    contexts where multiple coroutines share an event loop thread.

    Typical lifecycle per session::

        session = SessionStateManager()

        # --- Utterance A arrives ---
        req_id_a = session.issue_request("utt-001")
        intent   = await orchestrate(transcript_a, session=session)
        result_a = await execute_with_fencing(intent, session=session, ...)
        # result_a is the backend response (or None if superseded)

        # --- Utterance B arrives while A is still in-flight ---
        req_id_b = session.issue_request("utt-002")  # supersedes A
        # When result_a finally lands, session.resolve(req_id_a, ...) -> None
    """

    def __init__(self, log_file: Optional[str] = None, run_id: Optional[str] = None) -> None:
        """
        Args:
            log_file: Optional path to write "stale-result-discarded" JSON log
                      lines. If None, events are only emitted to the logger.
            run_id:   Optional run identifier to include in logs for timeline reconstruction.
        """
        self._current: Optional[str] = None
        self._lock = threading.Lock()
        self._log_file: Optional[Path] = Path(log_file) if log_file else None
        self._seq = 0
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_request_id(self) -> Optional[str]:
        """The request_id of the most recently issued utterance."""
        return self._current

    def issue_request(self, utterance_id: Optional[str] = None) -> str:
        """
        Register a new user utterance, superseding any previous request.

        Args:
            utterance_id: Caller-supplied ID (e.g. ASR turn ID). If None,
                          a UUID-based ID is generated automatically.

        Returns:
            The new request_id (same value as utterance_id when provided).
        """
        new_id = utterance_id if utterance_id is not None else f"req-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._current = new_id
        logger.info("SessionState: issued new request_id=%s", new_id)
        return new_id

    def is_stale(self, request_id: str) -> bool:
        """
        Return True if *request_id* no longer matches the current utterance.

        A result is stale when a newer utterance has already been issued —
        meaning the user has moved on and the old result is irrelevant.
        """
        with self._lock:
            return self._current != request_id

    def resolve(
        self,
        request_id: str,
        result: Any,
    ) -> Any:
        """
        Gate a tool result through the fencing check.

        Args:
            request_id: The request_id that was stamped on the tool call when
                        it was dispatched.
            result:     The payload returned by the backend tool.

        Returns:
            *result* unchanged if *request_id* still matches the current
            utterance, otherwise ``None`` (the result is discarded).

        Side-effects:
            Logs a ``stale-result-discarded`` structured JSON event (to
            ``logger`` and to ``log_file`` if configured) when the result
            is discarded.
        """
        with self._lock:
            current = self._current
            stale = current != request_id

        if stale:
            self._seq += 1
            record: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "seq": self._seq,
                "run_id": self.run_id,
                "stage": "stale-result-discarded",
                "event": "stale-result-discarded",
                "discarded_request_id": request_id,
                "current_request_id": current,
            }
            logger.warning(
                "Stale tool result discarded — discarded_request_id=%s current_request_id=%s",
                request_id,
                current,
            )
            self._append_log(record)
            return None

        return result

    def reset(self) -> None:
        """Clear the current request_id (call at end-of-session or for testing)."""
        with self._lock:
            self._current = None
        logger.info("SessionState: reset")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_log(self, record: dict[str, Any]) -> None:
        """Append *record* as a JSON line to self._log_file (if configured)."""
        if self._log_file is None:
            return
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_file, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write session log to %s: %s", self._log_file, exc)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a friendly and efficient appointment booking assistant.
Your job is to help users check available appointment slots and book appointments.

You have access to two tools:
1. check_availability(date) — returns available time slots for the given date.
2. book(date, slot) — books an appointment for the given date and time slot.

Rules:
- If the user wants to know available slots for a date, call check_availability.
- If the user wants to book a specific date AND slot, call book.
- If you need more information (e.g. the user mentioned booking but didn't give a slot), ask politely.
- If the user is just greeting, chatting, or the request is unrelated to booking, respond conversationally.
- Always be concise, warm, and professional.
- Do NOT make up slots or booking IDs — rely on the tools for that.
"""

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling schema)
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check available appointment time slots for a given date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The date to check availability for, e.g. '2026-09-10' or 'tomorrow'.",
                    }
                },
                "required": ["date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book",
            "description": "Book an appointment for a given date and time slot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The date of the appointment, e.g. '2026-09-10'.",
                    },
                    "slot": {
                        "type": "string",
                        "description": "The time slot to book, e.g. '14:00'.",
                    },
                },
                "required": ["date", "slot"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Heuristic fallback parser (no external API required)
# ---------------------------------------------------------------------------

# Regex patterns for date-like strings (ISO, "tomorrow", "Monday", month names, etc.)
_DATE_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|"
    r"today|tomorrow|yesterday|"
    r"(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?)\b",
    re.IGNORECASE,
)

# Regex patterns for time slots like 09:00, 9am, 2:30pm, 14:00
_SLOT_PATTERN = re.compile(
    r"\b(\d{1,2}:\d{2}(?:\s*[ap]m)?|\d{1,2}\s*[ap]m)\b",
    re.IGNORECASE,
)

# Keywords that signal booking intent
_BOOK_KEYWORDS = re.compile(
    r"\b(book|schedule|reserve|confirm|make\s+an?\s+appointment|set\s+up|arrange)\b",
    re.IGNORECASE,
)

# Keywords that signal availability-check intent
_AVAILABILITY_KEYWORDS = re.compile(
    r"\b(availab|free|open|slot|time|when|can\s+i|what\s+time|show\s+me|check)\b",
    re.IGNORECASE,
)


def _heuristic_parse(transcript: str) -> dict[str, Any] | str:
    """
    Deterministic intent parser — used when OpenAI is not configured or in tests.

    Decision tree:
      1. If book keywords + date + slot  -> tool_call: book
      2. If book keywords + date (no slot) -> direct: ask for slot
      3. If availability keywords + date  -> tool_call: check_availability
      4. If only date detected           -> tool_call: check_availability
      5. Otherwise                       -> direct: greeting / clarification
    """
    dates = _DATE_PATTERN.findall(transcript)
    slots = _SLOT_PATTERN.findall(transcript)
    has_book = bool(_BOOK_KEYWORDS.search(transcript))
    has_avail = bool(_AVAILABILITY_KEYWORDS.search(transcript))

    date = dates[0] if dates else None
    slot = slots[0] if slots else None

    if has_book and date and slot:
        return {"tool": "book", "args": {"date": date, "slot": slot}}

    if has_book and date and not slot:
        return f"I'd love to book an appointment for {date}! Which time slot would you prefer?"

    if (has_avail or date) and date:
        return {"tool": "check_availability", "args": {"date": date}}

    if has_book and not date:
        return "Sure, I can help you book an appointment! What date are you looking at?"

    if has_avail and not date:
        return "I can check availability for you! Which date are you interested in?"

    # Generic fallback
    return "Hello! I'm your booking assistant. I can help you check available slots or book an appointment. What would you like to do?"


# ---------------------------------------------------------------------------
# OpenAI-based orchestration
# ---------------------------------------------------------------------------


async def _openai_orchestrate(
    transcript: str,
    api_key: str,
    model: str,
    base_url: str,
) -> dict[str, Any] | str:
    """Call OpenAI chat completions with tool definitions and parse the result."""
    try:
        from openai import AsyncOpenAI  # import lazily so module loads without openai installed

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            tools=cast(Any, TOOLS),
            tool_choice="auto",
        )

        message = response.choices[0].message

        # If the model chose a tool call
        if message.tool_calls:
            tc = message.tool_calls[0]
            fn = getattr(tc, "function", None)
            if fn is not None:
                tool_name = getattr(fn, "name", "")
                raw_args = getattr(fn, "arguments", "{}")
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
                logger.info("OpenAI tool_call: tool=%s args=%s", tool_name, args)
                return {"tool": tool_name, "args": args}

        # Otherwise it's a direct conversational response
        content = message.content or ""
        logger.info("OpenAI direct response: %s", content[:80])
        return content

    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAI orchestration failed (%s), falling back to heuristic.", exc)
        return _heuristic_parse(transcript)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


async def orchestrate(
    transcript: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    use_heuristic: bool = False,
    session: Optional[SessionStateManager] = None,
    utterance_id: Optional[str] = None,
) -> dict[str, Any] | str:
    """
    Orchestrate a booking assistant response from a transcript string.

    Args:
        transcript:    The user's spoken or typed transcript.
        api_key:       OpenAI API key. Defaults to OPENAI_API_KEY env var.
        model:         OpenAI model name. Defaults to OPENAI_MODEL env var (gpt-4o-mini).
        base_url:      OpenAI-compatible base URL. Defaults to OPENAI_BASE_URL env var.
        use_heuristic: Force heuristic parser even if API key is present (for tests).
        session:       Optional SessionStateManager for request-ID fencing. When
                       provided, a new request_id is issued for this utterance and
                       stamped into any returned tool_call intent under the
                       ``"request_id"`` key.
        utterance_id:  Caller-supplied utterance correlation ID forwarded to
                       ``session.issue_request()``. Auto-generated when None.

    Returns:
        Either a tool_call intent dict ``{"tool": str, "args": dict, "request_id"?: str}``
        (``request_id`` present only when *session* is provided)
        or a direct response string.
    """
    # Issue a new request_id for this utterance if fencing is active
    active_request_id: Optional[str] = None
    if session is not None:
        active_request_id = session.issue_request(utterance_id)
        logger.info(
            "Orchestrating utterance request_id=%s transcript=%.80s",
            active_request_id,
            transcript,
        )

    resolved_key = api_key or os.getenv("OPENAI_API_KEY", "")
    resolved_model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    resolved_base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # Use heuristic if forced or no API key is available
    if use_heuristic or not resolved_key or resolved_key.startswith("your_") or os.getenv("FORCE_HEURISTIC", "true").lower() in ("true", "1", "yes"):
        logger.info("Using heuristic parser for transcript: %s", transcript[:80])
        result: dict[str, Any] | str = _heuristic_parse(transcript)
    else:
        result = await _openai_orchestrate(
            transcript=transcript,
            api_key=resolved_key,
            model=resolved_model,
            base_url=resolved_base_url,
        )

    # Stamp the active request_id into tool_call intents so downstream
    # execute_with_fencing() can match results to the correct utterance.
    if active_request_id is not None and isinstance(result, dict):
        result = {**result, "request_id": active_request_id}

    return result


# ---------------------------------------------------------------------------
# Backend caller helper — bridges orchestration decision to backend API
# ---------------------------------------------------------------------------


async def call_backend_tool(
    tool_intent: dict[str, Any],
    *,
    base_url: str = "http://localhost:8000",
    request_id: Optional[str] = None,
    delay_override: Optional[float] = None,
) -> dict[str, Any]:
    """
    Execute a tool_call intent against the FastAPI booking backend.

    Args:
        tool_intent:    Dict with keys "tool" and "args" from orchestrate().
        base_url:       Base URL of the backend server.
        request_id:     Correlation ID (generated if not provided).
        delay_override: Override artificial delay for /check-availability (useful in tests).

    Returns:
        The parsed JSON response from the backend.

    Raises:
        ValueError: If tool name is unknown.
        httpx.HTTPError: On HTTP/network failures.
    """
    import uuid as _uuid

    tool = tool_intent.get("tool")
    args = dict(tool_intent.get("args", {}))
    req_id = request_id or f"req-{_uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        if tool == "check_availability":
            payload: dict[str, Any] = {
                "date": args.get("date", ""),
                "request_id": req_id,
            }
            if delay_override is not None:
                payload["delay"] = delay_override
            response = await client.post("/check-availability", json=payload)
            response.raise_for_status()
            return response.json()

        elif tool == "book":
            payload = {
                "date": args.get("date", ""),
                "slot": args.get("slot", ""),
                "request_id": req_id,
            }
            response = await client.post("/book", json=payload)
            response.raise_for_status()
            return response.json()

        else:
            raise ValueError(f"Unknown tool: {tool!r}")


# ---------------------------------------------------------------------------
# Fenced tool execution — orchestrate + call + fencing check in one step
# ---------------------------------------------------------------------------


async def execute_with_fencing(
    tool_intent: dict[str, Any],
    *,
    session: Optional[SessionStateManager],
    base_url: str = "http://localhost:8000",
    delay_override: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """
    Execute a tool_call intent against the backend with session state fencing.

    Extracts the ``request_id`` that ``orchestrate()`` stamped into *tool_intent*
    (when a session was provided). After the backend responds, gates the result
    through ``session.resolve()`` before returning it.

    Args:
        tool_intent:    Dict produced by ``orchestrate()`` with keys ``"tool"``,
                        ``"args"``, and optionally ``"request_id"``.
        session:        Active SessionStateManager. Pass ``None`` to skip fencing
                        (result is always returned).
        base_url:       Base URL of the FastAPI booking backend.
        delay_override: Override artificial delay for ``/check-availability``
                        (useful in tests to avoid 4 s waits).

    Returns:
        The backend JSON response if the result is still current, or ``None``
        if the utterance has been superseded by a newer one (stale).
    """
    # Extract the request_id stamped by orchestrate()
    request_id: Optional[str] = tool_intent.get("request_id")

    # Hit the backend
    raw_result = await call_backend_tool(
        tool_intent,
        base_url=base_url,
        request_id=request_id,
        delay_override=delay_override,
    )

    # Gate through fencing check
    if session is not None and request_id is not None:
        return session.resolve(request_id, raw_result)

    # No session / no request_id — pass through unconditionally
    return raw_result


# ---------------------------------------------------------------------------
# Natural Spoken Response Generation (for TTS)
# ---------------------------------------------------------------------------


def _format_spoken_time(slot: str) -> str:
    """Convert 24-hour time or numeric slot into natural conversational English."""
    slot = slot.strip()
    try:
        if ":" in slot:
            h_str, m_str = slot.split(":", 1)
            h = int(h_str)
            m = int(m_str)
            period = "AM" if h < 12 else "PM"
            h12 = h % 12
            if h12 == 0:
                h12 = 12
            if m == 0:
                return f"{h12} {period}"
            return f"{h12}:{m:02d} {period}"
    except Exception:
        pass
    return slot


def format_spoken_response(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
) -> str:
    """
    Format raw backend tool results into natural spoken English for TTS,
    avoiding raw dicts, military timestamps, or robotic lists.
    """
    if tool_name == "check_availability":
        date = args.get("date", "that day")
        raw_slots = result.get("available_slots", []) if isinstance(result, dict) else []
        if not raw_slots:
            return f"I checked availability for {date}, but unfortunately there are no open slots right now. Would another day work for you?"

        spoken_slots = [_format_spoken_time(s) for s in raw_slots]
        if len(spoken_slots) == 1:
            return f"I found an open slot on {date} at {spoken_slots[0]}. Would that time work for you?"
        elif len(spoken_slots) == 2:
            return f"I found two open slots on {date} — {spoken_slots[0]} or {spoken_slots[1]}. Would either of those work?"
        else:
            first_choice = spoken_slots[1] if len(spoken_slots) > 1 else spoken_slots[0]
            second_choice = spoken_slots[3] if len(spoken_slots) > 3 else spoken_slots[-1]
            return f"I found a few open slots on {date} — would {first_choice} or {second_choice} work for you?"

    elif tool_name == "book":
        date = args.get("date", "")
        slot = _format_spoken_time(args.get("slot", ""))
        confirmation = result.get("confirmation", "") if isinstance(result, dict) else str(result)
        return f"Great, you're all booked for {date} at {slot}! Your confirmation number is {confirmation}."

    if isinstance(result, dict):
        if "error" in result:
            return f"I encountered an issue: {result['error']}"
        return str(result)
    return str(result)


async def generate_spoken_response(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any],
    user_transcript: str = "",
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """
    Generate natural spoken speech from tool results via OpenAI LLM if available,
    falling back to natural conversational formatting.
    """
    resolved_key = api_key or os.getenv("OPENAI_API_KEY", "")
    force_heuristic = os.getenv("FORCE_HEURISTIC", "true").lower() in ("true", "1", "yes")

    if not force_heuristic and resolved_key and not resolved_key.startswith("your_"):
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=resolved_key,
                base_url=base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )
            resp = await client.chat.completions.create(
                model=model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a friendly appointment booking voice assistant speaking over the phone. "
                            "Convert this backend tool execution result into ONE short, warm spoken sentence "
                            "(under 20 words) suitable for Rime text-to-speech. "
                            "Never include markdown, bullet points, technical json, or debug prefixes like 'Tool result:'."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"User request: {user_transcript}\n"
                            f"Tool executed: {tool_name}\n"
                            f"Arguments: {args}\n"
                            f"Backend response: {result}\n"
                        ),
                    },
                ],
                max_tokens=60,
                temperature=0.7,
            )
            content = resp.choices[0].message.content or ""
            cleaned = content.strip().replace('"', '')
            if cleaned and not cleaned.lower().startswith("tool result:"):
                return cleaned
        except Exception as exc:
            logger.debug("LLM spoken generation failed (%s), using natural template", exc)

    return format_spoken_response(tool_name, args, result)

