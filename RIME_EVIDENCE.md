# RIME Evidence: Interrupt & State Fencing Verification

## Purpose

This document describes the repeatable test harness that proves Snapback's
barge-in interrupt handling and request-ID state fencing work correctly
end-to-end, producing structured log evidence for every run.

---

## System Under Test

| Module | Role |
|---|---|
| [`backend.py`](file:///c:/Users/mdari/snapback/backend.py) | FastAPI booking backend — `/check-availability`, `/book` |
| [`orchestrator.py`](file:///c:/Users/mdari/snapback/orchestrator.py) | LLM orchestration + `SessionStateManager` state fencing |
| [`agent.py`](file:///c:/Users/mdari/snapback/agent.py) | `StructuredTimelineLogger` — pipeline event logging |

---

## Repeatable Test Command

```powershell
# Default run (interrupt after 0.5s, threshold 50ms)
python harness.py

# Custom timing
python harness.py --interrupt-delay 1.0 --threshold-ms 100

# Custom log directory (e.g. Docker volume mount)
python harness.py --log-dir /logs

# Show all options
python harness.py --help
```

**Exit codes:** `0` = all checks pass · `1` = one or more checks fail · `2` = harness error

---

## What the Harness Tests

The harness drives a scripted two-utterance barge-in scenario entirely
in-process (no running server required):

```
Step 1  Utterance 1 fires
        orchestrate("Check availability for 2026-12-01", session=session, utterance_id=utt-A-*)
        -> tool_call: check_availability  request_id=utt-A-*
        Backend call starts in background.
        Pipeline logs: tool-start, tts-start

Step 2  Wait N seconds (simulates TTS audio playing while user interrupts)

Step 3  Interrupt fires
        Pipeline logs: interrupt-detected  (timestamp T0)
        In-flight asyncio task is cancelled.
        Pipeline logs: tts-cancelled       (timestamp T1)

Step 4  Utterance 2 supersedes utterance 1
        orchestrate("Book me for 2026-12-05 at 14:00", session=session, utterance_id=utt-B-*)
        session.current_request_id -> utt-B-*
        Tool2 executes immediately -> session.resolve(utt-B-*) -> FRESH result
        Pipeline logs: tts-end

Step 5  Utterance 1's (stale) result arrives
        session.resolve(utt-A-*, result) -> stale -> returns None
        Session log: stale-result-discarded event
```

---

## Verification Checks

All three checks parse log files from `logs/harness/*.log`.

### [A]  tts-cancelled within threshold of interrupt-detected

Reads `logs/harness/agent.log`.

Expected log events (by `stage` field):

```json
{"timestamp":"2026-09-07T18:48:30.460711+00:00","stage":"interrupt-detected","state":"tts-speaking",...}
{"timestamp":"2026-09-07T18:48:30.469231+00:00","stage":"tts-cancelled","state":"listening","latency_ms":8.52,...}
```

**Assertion:** `|T(tts-cancelled) - T(interrupt-detected)| <= threshold_ms`

---

### [B]  Exactly one stale-result-discarded event

Reads `logs/harness/session.log`.

Expected log event (by `event` field):

```json
{
  "timestamp": "2026-09-07T18:48:30.479000+00:00",
  "event": "stale-result-discarded",
  "discarded_request_id": "utt-A-b83d4069",
  "current_request_id":   "utt-B-72d410ba"
}
```

**Assertion:** `count(stale-result-discarded events) == 1`

---

### [C]  Final tool call request_id matches utterance 2

Reads `logs/harness/backend.log`.

Expected log event (by `event` field):

```json
{
  "timestamp": "...",
  "event": "request_out",
  "request_id": "utt-B-72d410ba",
  "endpoint": "/book",
  "status_code": 200,
  "response": {"status": "confirmed", "booking_id": "BKG-...", "request_id": "utt-B-72d410ba"}
}
```

**Assertion:** A `request_out` event with `request_id == utt2_id` exists **and**
`session.resolve(utt2_id, result)` returns non-`None` (confirmed fresh in-memory).

---

## Example Report Output

```
+======================================================================+
|             SNAPBACK INTERRUPT + FENCING HARNESS REPORT              |
+======================================================================+

  Run ID          : harness-20260906T184829-b4b2a4
  Started         : 2026-09-06T18:48:29.940549+00:00
  Log directory   : logs\harness
  interrupt-delay : 0.50 s
  threshold-ms    : 50 ms
  utt1 ID         : utt-A-b83d4069
  utt2 ID         : utt-B-72d410ba

----------------------------------------------------------------------
  [A]  tts-cancelled timestamp within threshold of interrupt-detected
       delta(interrupt->cancelled) = 8.520 ms   threshold = 50 ms
       -> PASS

  [B]  Exactly one stale-result-discarded event in session log
       stale-result-discarded count = 1   (expected = 1)
         discarded_request_id = utt-A-b83d4069
         current_request_id   = utt-B-72d410ba
       -> PASS

  [C]  Final tool call request_id in backend log matches utterance 2
       utt2_id = utt-B-72d410ba
       found in backend log    = True
       backend echoed id       = utt-B-72d410ba
       session resolved fresh  = True
       -> PASS

----------------------------------------------------------------------
  Latency summary:
    interrupt-detected -> tts-cancelled : 8.738 ms

----------------------------------------------------------------------
  OVERALL: PASS -- all checks passed
----------------------------------------------------------------------
```

A plain-text copy of the report is also saved to `logs/harness/harness_report.txt`
on every run.

---

## Log File Layout

After each run, `logs/harness/` contains:

| File | Written by | Key events |
|---|---|---|
| `agent.log` | `StructuredTimelineLogger` | `tool-start`, `tts-start`, `interrupt-detected`, `tts-cancelled`, `tts-end` |
| `session.log` | `SessionStateManager` | `stale-result-discarded` |
| `backend.log` | FastAPI middleware | `request_in`, `request_out` (both utterances) |
| `harness_report.txt` | Harness verifier | Human-readable pass/fail summary |

> The log directory is **cleared before every run** (unless `--keep-logs` is passed).

---

## Re-running

```powershell
# Re-run immediately (logs cleared automatically)
python harness.py

# Keep previous logs and append (IDs differ each run, so events accumulate)
python harness.py --keep-logs

# Stricter latency gate
python harness.py --threshold-ms 20

# Longer interrupt window (more realistic network latency simulation)
python harness.py --interrupt-delay 2.0
```
