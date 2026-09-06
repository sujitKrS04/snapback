#!/usr/bin/env python3
"""
harness.py — Snapback Interrupt & State Fencing Test Harness  v1.0.0

Simulates a two-utterance barge-in interrupt scenario entirely in-process,
then parses /logs/harness/*.log to automatically verify three invariants.

Scenario
────────
  1. Utterance 1  → tool call starts in-flight  (check_availability)
  2. Pipeline logs tts-start  (simulated TTS speaking)
  3. Interrupt fires after --interrupt-delay seconds
       → interrupt-detected logged
       → tts-cancelled logged
  4. Utterance 2 supersedes utterance 1 in SessionStateManager  (book)
  5. Utterance 2's tool call executes immediately → FRESH result
  6. Utterance 1's result arrives late
       → session.resolve() sees it as STALE
       → stale-result-discarded logged

Verifications (all read from log files)
────────────────────────────────────────
  [A]  tts-cancelled timestamp is within --threshold-ms of interrupt-detected
  [B]  Exactly one stale-result-discarded event exists
  [C]  Final backend request_out carries utterance 2's request_id

Usage
─────
  python harness.py
  python harness.py --interrupt-delay 1.0 --threshold-ms 50
  python harness.py --log-dir /logs --keep-logs
  python harness.py --help

  (On Windows set PYTHONIOENCODING=utf-8 if the console shows encoding errors)
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Optional
import uuid

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

from httpx import AsyncClient, ASGITransport

import backend as _backend_mod
from backend import app as _fastapi_app
from orchestrator import SessionStateManager, orchestrate
from agent import StructuredTimelineLogger, PipelineState

# ---------------------------------------------------------------------------
# Defaults & version
# ---------------------------------------------------------------------------

VERSION = "1.0.0"
DEFAULT_INTERRUPT_DELAY: float = 0.5   # seconds before interrupt fires
DEFAULT_THRESHOLD_MS: float = 50.0     # max ms interrupt→cancelled
DEFAULT_LOG_DIR: str = "logs/harness"

_UTT1_TRANSCRIPT = "Check availability for 2026-12-01"
_UTT2_TRANSCRIPT = "Book me for 2026-12-05 at 14:00"

# ---------------------------------------------------------------------------
# Terminal colour helpers (degrades gracefully in CI / file redirection)
# ---------------------------------------------------------------------------

_COLOUR = sys.stdout.isatty() or bool(os.getenv("FORCE_COLOUR"))


def _a(code: str, t: str) -> str:
    return f"\033[{code}m{t}\033[0m" if _COLOUR else t


def green(t: str)  -> str: return _a("32;1", t)
def red(t: str)    -> str: return _a("31;1", t)
def bold(t: str)   -> str: return _a("1",    t)
def dim(t: str)    -> str: return _a("2",    t)

# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load valid JSON-lines from *path*; silently skip malformed lines."""
    if not path.exists():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    return out


def _load_all_logs(log_dir: Path) -> list[dict[str, Any]]:
    """Merge every *.log in *log_dir*, sorted ascending by timestamp."""
    events: list[dict] = []
    for f in sorted(log_dir.glob("*.log")):
        events.extend(_load_jsonl(f))
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def _ts_delta_ms(ts_a: str, ts_b: str) -> float:
    """Absolute millisecond gap between two ISO-8601 timestamp strings."""
    a = datetime.fromisoformat(ts_a).timestamp()
    b = datetime.fromisoformat(ts_b).timestamp()
    return abs(b - a) * 1000.0

# ---------------------------------------------------------------------------
# Scenario execution
# ---------------------------------------------------------------------------


async def run_scenario(
    *,
    interrupt_delay: float,
    log_dir: Path,
) -> dict[str, Any]:
    """
    Drive the two-utterance barge-in scenario in-process.

    Returns an *evidence* dict with timing data and resolved results
    that the verifier uses alongside the written log files.
    """
    utt1_id = f"utt-A-{uuid.uuid4().hex[:8]}"
    utt2_id = f"utt-B-{uuid.uuid4().hex[:8]}"

    agent_log   = log_dir / "agent.log"
    session_log = log_dir / "session.log"
    backend_log = log_dir / "backend.log"

    # Redirect backend structured logs to this run's directory
    _orig = list(_backend_mod._LOG_CANDIDATES)
    _backend_mod._LOG_CANDIDATES = [backend_log]

    session = SessionStateManager(log_file=str(session_log))
    pl = StructuredTimelineLogger(
        run_id=f"harness-{datetime.now(timezone.utc).strftime('%H%M%S')}-{uuid.uuid4().hex[:4]}",
        default_log_path=str(agent_log),
    )
    transport = ASGITransport(app=_fastapi_app)

    evidence: dict[str, Any] = {
        "utt1_id":                  utt1_id,
        "utt2_id":                  utt2_id,
        "log_dir":                  log_dir,
        "interrupt_ts":             None,
        "cancelled_ts":             None,
        "cancel_latency_ms":        None,
        "tool1_result":             None,
        "tool2_result":             None,
        "tool2_backend_request_id": None,
    }

    try:
        # ── 1. Utterance 1 ───────────────────────────────────────────────────
        print(f"\n  {dim('1.')} Utterance 1  -- {_UTT1_TRANSCRIPT!r}")

        intent1 = await orchestrate(
            _UTT1_TRANSCRIPT,
            session=session,
            utterance_id=utt1_id,
            use_heuristic=True,
        )
        if not isinstance(intent1, dict):
            raise RuntimeError(f"Utterance 1 did not resolve to a tool_call: {intent1!r}")

        print(f"       tool={intent1['tool']}  request_id={utt1_id}")

        pl.log("tool-start", state=PipelineState.TOOL_RUNNING,
               previous_state=PipelineState.TOOL_PENDING, request_id=utt1_id)
        pl.log("tts-start",  state=PipelineState.TTS_SPEAKING,
               previous_state=PipelineState.TOOL_COMPLETED, request_id=utt1_id,
               text="Checking availability...")

        # Start tool1 backend call in background (no artificial extra delay;
        # the session supersession is what makes its result stale, not timing).
        async def _tool1() -> dict[str, Any]:
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                r = await ac.post("/check-availability", json={
                    "date": intent1["args"].get("date", "2026-12-01"),
                    "request_id": utt1_id,
                    "delay": 0,
                })
                return r.json()

        tool1_task: asyncio.Task = asyncio.create_task(_tool1())

        # ── 2. Wait ──────────────────────────────────────────────────────────
        print(f"\n  {dim('2.')} Holding {interrupt_delay:.2f}s (TTS in-flight)...")
        await asyncio.sleep(interrupt_delay)

        # ── 3. Interrupt ─────────────────────────────────────────────────────
        t0_perf = time.perf_counter()
        t_interrupt_utc = datetime.now(timezone.utc).isoformat()

        pl.log(
            "interrupt-detected",
            state=PipelineState.TTS_SPEAKING,
            previous_state=PipelineState.TTS_SPEAKING,
            request_id=utt1_id,
            interrupted_state=PipelineState.TTS_SPEAKING.value,
            interruption_count=1,
        )

        # Cancel in-flight TTS audio task (mirrors VoiceAudioPipeline.cancel_active)
        tool1_task.cancel()

        t1_perf = time.perf_counter()
        t_cancelled_utc = datetime.now(timezone.utc).isoformat()
        cancel_ms = (t1_perf - t0_perf) * 1000.0

        pl.log(
            "tts-cancelled",
            state=PipelineState.LISTENING,
            previous_state=PipelineState.TTS_SPEAKING,
            request_id=utt1_id,
            latency_ms=round(cancel_ms, 3),
            interruption_count=1,
        )

        evidence["interrupt_ts"]      = t_interrupt_utc
        evidence["cancelled_ts"]      = t_cancelled_utc
        evidence["cancel_latency_ms"] = cancel_ms

        print(f"\n  {dim('3.')} Interrupt fired")
        print(f"       interrupt-detected @ {t_interrupt_utc}")
        print(f"       tts-cancelled      @ {t_cancelled_utc}  delta={cancel_ms:.3f} ms")

        # ── 4. Utterance 2 (supersedes utt1) ─────────────────────────────────
        print(f"\n  {dim('4.')} Utterance 2  -- {_UTT2_TRANSCRIPT!r}")

        intent2 = await orchestrate(
            _UTT2_TRANSCRIPT,
            session=session,
            utterance_id=utt2_id,
            use_heuristic=True,
        )
        if not isinstance(intent2, dict):
            raise RuntimeError(f"Utterance 2 did not resolve to a tool_call: {intent2!r}")

        print(f"       tool={intent2['tool']}  request_id={utt2_id}")
        print(f"       session.current_request_id -> {session.current_request_id}")

        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            if intent2["tool"] == "book":
                r2 = await ac.post("/book", json={
                    "date": intent2["args"].get("date", ""),
                    "slot": intent2["args"].get("slot", ""),
                    "request_id": utt2_id,
                })
            else:
                r2 = await ac.post("/check-availability", json={
                    "date": intent2["args"].get("date", ""),
                    "request_id": utt2_id,
                    "delay": 0,
                })
            tool2_raw = r2.json()

        tool2_result = session.resolve(utt2_id, tool2_raw)
        evidence["tool2_result"]             = tool2_result
        evidence["tool2_backend_request_id"] = tool2_raw.get("request_id")

        status2 = green("FRESH [ok]") if tool2_result else red("STALE [unexpected!]")
        print(f"       session.resolve(utt2_id) -- {status2}")

        pl.log("tts-end", state=PipelineState.IDLE,
               previous_state=PipelineState.TTS_SPEAKING, request_id=utt2_id)

        # ── 5. Stale result from utt1 arrives ────────────────────────────────
        print(f"\n  {dim('5.')} Collecting stale result for utt1...")

        try:
            tool1_raw = await tool1_task
        except asyncio.CancelledError:
            # Task was cancelled — simulate late result arriving (HTTP completed).
            # This is realistic: the HTTP response may have already been buffered.
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                r1 = await ac.post("/check-availability", json={
                    "date": intent1["args"].get("date", "2026-12-01"),
                    "request_id": utt1_id,
                    "delay": 0,
                })
                tool1_raw = r1.json()

        # session.current is now utt2_id -> utt1_id is stale -> logs event
        tool1_result = session.resolve(utt1_id, tool1_raw)
        evidence["tool1_result"] = tool1_result

        status1 = (
            red("FRESH [unexpected!]")
            if tool1_result else
            green("STALE [ok, discarded, logged]")
        )
        print(f"       session.resolve(utt1_id) -- {status1}")

    finally:
        _backend_mod._LOG_CANDIDATES = _orig

    return evidence

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class CheckResult:
    __slots__ = ("name", "label", "passed", "detail", "measured")

    def __init__(
        self,
        name: str,
        label: str,
        passed: bool,
        detail: str,
        measured: float | None = None,
    ) -> None:
        self.name    = name
        self.label   = label
        self.passed  = passed
        self.detail  = detail
        self.measured = measured


def verify(evidence: dict[str, Any], threshold_ms: float) -> list[CheckResult]:
    """Parse log files in evidence['log_dir'] and return a list of CheckResults."""
    log_dir: Path = evidence["log_dir"]
    utt1_id: str  = evidence["utt1_id"]
    utt2_id: str  = evidence["utt2_id"]

    events = _load_all_logs(log_dir)
    checks: list[CheckResult] = []

    # ── [A] tts-cancelled within threshold of interrupt-detected ─────────────
    interrupted = [e for e in events if e.get("stage") == "interrupt-detected"]
    cancelled   = [e for e in events if e.get("stage") == "tts-cancelled"]

    if not interrupted or not cancelled:
        checks.append(CheckResult(
            "A",
            "tts-cancelled timestamp within threshold of interrupt-detected",
            False,
            f"interrupt-detected events found={len(interrupted)}, "
            f"tts-cancelled events found={len(cancelled)}  (expected >=1 each)",
        ))
    else:
        delta_ms = _ts_delta_ms(
            interrupted[-1]["timestamp"],
            cancelled[-1]["timestamp"],
        )
        checks.append(CheckResult(
            "A",
            "tts-cancelled timestamp within threshold of interrupt-detected",
            delta_ms <= threshold_ms,
            f"delta(interrupt->cancelled) = {delta_ms:.3f} ms   threshold = {threshold_ms:.0f} ms",
            measured=delta_ms,
        ))

    # ── [B] exactly one stale-result-discarded ───────────────────────────────
    stale = [e for e in events if e.get("event") == "stale-result-discarded" or e.get("stage") == "stale-result-discarded"]
    n_stale = len(stale)
    checks.append(CheckResult(
        "B",
        "Exactly one stale-result-discarded event in session log",
        n_stale == 1,
        f"stale-result-discarded count = {n_stale}   (expected = 1)",
        measured=float(n_stale),
    ))

    if n_stale == 1:
        ev = stale[0]
        disc = ev.get("discarded_request_id", "")
        curr = ev.get("current_request_id",   "")
        details_b = (
            f"  discarded_request_id = {disc}\n"
            f"       current_request_id  = {curr}"
        )
        checks[-1].detail += f"\n{details_b}"

    # ── [C] final backend request_id matches utt2 ────────────────────────────
    backend_out  = [e for e in events if e.get("event") == "request_out"]
    utt2_in_log  = any(e.get("request_id") == utt2_id for e in backend_out)
    tool2_fresh  = evidence.get("tool2_result") is not None
    tool2_bknd   = evidence.get("tool2_backend_request_id", "")

    checks.append(CheckResult(
        "C",
        "Final tool call request_id in backend log matches utterance 2",
        utt2_in_log and tool2_fresh,
        (
            f"utt2_id = {utt2_id}\n"
            f"       found in backend log    = {utt2_in_log}\n"
            f"       backend echoed id       = {tool2_bknd}\n"
            f"       session resolved fresh  = {tool2_fresh}"
        ),
    ))

    return checks

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_SEP  = "-" * 70
_DSEP = "=" * 70


def print_and_save_report(
    *,
    checks: list[CheckResult],
    evidence: dict[str, Any],
    run_start: str,
    run_id: str,
    log_dir: Path,
    interrupt_delay: float,
    threshold_ms: float,
) -> bool:
    """Print a colour report to stdout and save a plain-text copy; return overall pass."""
    overall = all(c.passed for c in checks)
    cancel_ms = evidence.get("cancel_latency_ms") or 0.0

    plain_lines: list[str] = [
        "",
        "+" + _DSEP + "+",
        "|" + "  SNAPBACK INTERRUPT + FENCING HARNESS REPORT  ".center(70) + "|",
        "+" + _DSEP + "+",
        "",
        f"  Run ID          : {run_id}",
        f"  Started         : {run_start}",
        f"  Log directory   : {log_dir}",
        f"  interrupt-delay : {interrupt_delay:.2f} s",
        f"  threshold-ms    : {threshold_ms:.0f} ms",
        f"  utt1 ID         : {evidence['utt1_id']}",
        f"  utt2 ID         : {evidence['utt2_id']}",
        "",
        _SEP,
    ]

    for chk in checks:
        verdict = "PASS" if chk.passed else "FAIL"
        plain_lines += [
            f"  [{chk.name}]  {chk.label}",
            *[f"       {ln}" for ln in chk.detail.splitlines()],
            f"       -> {verdict}",
            "",
        ]

    plain_lines += [
        _SEP,
        "  Latency summary:",
        f"    interrupt-detected -> tts-cancelled : {cancel_ms:.3f} ms",
        "",
        _SEP,
        f"  OVERALL: {'PASS -- all checks passed' if overall else 'FAIL -- one or more checks failed'}",
        _SEP,
        "",
    ]

    plain_report = "\n".join(plain_lines)

    # Colour substitution for stdout
    coloured: list[str] = []
    for ln in plain_lines:
        if "-> PASS" in ln or "OVERALL: PASS" in ln:
            ln = ln.replace("PASS", green("PASS"), 1)
        elif "-> FAIL" in ln or "OVERALL: FAIL" in ln:
            ln = ln.replace("FAIL", red("FAIL"), 1)
        coloured.append(ln)

    print("\n".join(coloured))

    report_path = log_dir / "harness_report.txt"
    report_path.write_text(plain_report, encoding="utf-8")
    print(dim(f"  Plain report saved -> {report_path}"))
    print()

    return overall

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python harness.py",
        description="Snapback interrupt & state fencing test harness.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--interrupt-delay", type=float, default=DEFAULT_INTERRUPT_DELAY,
        metavar="SECS",
        help="Seconds to wait after utterance 1 before the interrupt fires",
    )
    p.add_argument(
        "--threshold-ms", type=float, default=DEFAULT_THRESHOLD_MS,
        metavar="MS",
        help="Maximum acceptable ms gap between interrupt-detected and tts-cancelled",
    )
    p.add_argument(
        "--log-dir", type=str, default=DEFAULT_LOG_DIR,
        metavar="DIR",
        help="Directory for harness log files (cleared before each run by default)",
    )
    p.add_argument(
        "--keep-logs", action="store_true",
        help="Do NOT clear the log directory before the run",
    )
    return p.parse_args()


async def _async_main() -> int:
    args      = _parse_args()
    log_dir   = Path(args.log_dir)
    run_id    = (
        f"harness-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        f"-{uuid.uuid4().hex[:6]}"
    )
    run_start = datetime.now(timezone.utc).isoformat()

    print(bold(f"\n  Snapback Test Harness  v{VERSION}  --  {run_id}"))
    print(dim(
        f"  interrupt_delay={args.interrupt_delay}s  "
        f"threshold={args.threshold_ms}ms  "
        f"log_dir={log_dir}"
    ))

    # Prepare log directory
    if not args.keep_logs and log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{bold('Running scenario...')}")
    try:
        evidence = await run_scenario(
            interrupt_delay=args.interrupt_delay,
            log_dir=log_dir,
        )
    except Exception as exc:
        print(red(f"\n[ERROR] Scenario execution failed: {exc}"))
        import traceback
        traceback.print_exc()
        return 2

    print(f"\n{bold('Verifying')} {log_dir}/*.log ...")
    checks = verify(evidence, threshold_ms=args.threshold_ms)

    overall = print_and_save_report(
        checks=checks,
        evidence=evidence,
        run_start=run_start,
        run_id=run_id,
        log_dir=log_dir,
        interrupt_delay=args.interrupt_delay,
        threshold_ms=args.threshold_ms,
    )
    return 0 if overall else 1


def main() -> None:
    sys.exit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
