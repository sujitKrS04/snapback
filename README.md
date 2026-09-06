# LiveKit Voice Agent (Deepgram ASR + Rime TTS + Barge-in Interruption)

A real-time LiveKit Python voice agent built for full-duplex conversational voice interactions with:
- WebRTC room connection using the official LiveKit Agents framework (`livekit-agents`).
- Streaming audio transcription via **Deepgram Streaming STT** (`nova-3`), streaming transcripts directly to `stdout` in real-time.
- Voice synthesis and room playback via the **official Rime TTS integration** (`livekit-plugins-rime`), with dynamic multi-provider switching and fallback to Deepgram TTS.
- **Edge-Case Hardened Barge-In**:
  - **Active TTS playback interruption**: Immediately cancels streaming and flushes queued room audio via `AudioSource.clear_queue()`.
  - **Double interrupts in a row**: Re-entrant, atomic interruption handling via `asyncio.Lock()` preventing race conditions.
  - **Interrupt before any tool call started**: Aborts turn in `tool-pending` state prior to tool execution, logging `tool-cancelled`.
  - **Interrupt after tool result arrived but before TTS started speaking**: In `tool-completed` state, discards tool results and cancels scheduled TTS before audio synthesis begins.
- **Deterministic Timeline Reconstruction**: Every log record contains a monotonic sequence counter (`seq`), unique session `run_id`, `stage`, `state`, `previous_state`, `active_tts_provider`, and high-precision ISO-8601 UTC timestamps.
- **Live Status & Provider Exposure**: `pipeline.status` dictionary exposes active provider, live state, speech status, tool status, and sequence counters.

---

## Organizer Preflight Verification: Rime Catalog & Transport

This table confirms that the exact combination used in `agent.py` matches Rime's live catalog specifications and the organizer's preflight check:

| Field | Configuration Value | Verified Against Rime Live Catalog | Running in Demo |
| :--- | :--- | :--- | :--- |
| **Model ID** | `coda` | Flagship conversational model (`TTSModels: Literal['mistv2', 'mistv3', 'coda']`) | `agent.py`: `os.getenv("RIME_MODEL", "coda")` |
| **Voice / Speaker** | `celeste` | American female conversational voice in Coda catalog (`rime/coda:celeste`) | `agent.py`: `os.getenv("RIME_VOICE", "celeste")` |
| **Language Code** | `eng` | English language code (`TTSLangs: Literal['eng', 'spa', 'fra', 'ger', 'hin']`) | `agent.py`: `os.getenv("RIME_LANG", "eng")` |
| **Sample Rate** | `22050` Hz | Native output sample rate for Rime Coda audio frames | `agent.py`: `rtc.AudioSource(sample_rate=22050, num_channels=1)` |
| **Transport (Default)**| HTTP Chunked Streaming | `https://users.rime.ai/v1/rime-tts` (low-latency chunked audio response) | `agent.py`: `use_websocket=False` (default) |
| **Transport (WebSocket)**| Bidirectional WS | `wss://users-ws.rime.ai` (real-time streaming with aligned transcripts) | `agent.py`: `use_websocket=True` (`RIME_USE_WEBSOCKET=true`) |

---

## State Machine & Interruption Lifecycle

```
       [IDLE]
         │  (speech-start)
         ▼
    [LISTENING] ◄───────────────────────────────────────────────┐
         │                                                      │
         │  (speech-end)                                        │
         ▼                                                      │
   [TOOL_PENDING] ────(interrupt before tool starts)───────────┤
         │                                                      │
         │  (tool-start)                                        │
         ▼                                                      │
   [TOOL_RUNNING] ────(interrupt during tool execution)────────┤
         │                                                      │
         │  (tool-result)                                       │
         ▼                                                      │
  [TOOL_COMPLETED] ───(interrupt after tool, before TTS)───────┤
         │                                                      │
         │  (tts-start)                                         │
         ▼                                                      │
   [TTS_SPEAKING] ────(interrupt during TTS playback)──────────┘
         │
         │  (tts-end)
         ▼
       [IDLE]
```

---

## Requirements & Setup

1. **Create and activate virtual environment**:
   ```bash
   python -m venv .venv
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   # Linux/macOS:
   source .venv/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   Fill in your credentials:
   ```ini
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=your_livekit_api_key
   LIVEKIT_API_SECRET=your_livekit_api_secret
   DEEPGRAM_API_KEY=your_deepgram_api_key
   RIME_API_KEY=your_rime_api_key

   # Rime Model Configuration (Preflight Verified)
   RIME_MODEL=coda
   RIME_VOICE=celeste
   RIME_LANG=eng
   RIME_SAMPLE_RATE=22050
   RIME_USE_WEBSOCKET=false

   # TTS Provider Switch (rime or deepgram)
   TTS_PROVIDER=rime
   TTS_FALLBACK_PROVIDER=deepgram
   ```

---

## Running the Agent

### Development Mode
Runs the agent worker in local development mode:
```bash
python agent.py dev
```

### Connect Mode (Explicit Room)
Connect directly to a specific room for testing:
```bash
python agent.py connect --room <ROOM_NAME>
```

### Production Start
```bash
python agent.py start
```

---

## Structured Timeline Log Format

All stage transitions are written to `logs/agent.log` (and `/logs/agent.log` if permissions allow) as valid JSON lines with complete timeline reconstruction metadata:

```json
{"timestamp": "2026-09-06T07:55:18.872000+00:00", "seq": 1, "run_id": "run-a1b2c3d4", "stage": "tts-start", "state": "tts-speaking", "previous_state": "idle", "active_tts_provider": "rime", "participant_id": "user-1"}
{"timestamp": "2026-09-06T07:55:18.875000+00:00", "seq": 2, "run_id": "run-a1b2c3d4", "stage": "interrupt-detected", "state": "tts-speaking", "previous_state": "tts-speaking", "active_tts_provider": "rime", "interrupted_state": "tts-speaking", "interruption_count": 1}
{"timestamp": "2026-09-06T07:55:18.881000+00:00", "seq": 3, "run_id": "run-a1b2c3d4", "stage": "tts-cancelled", "state": "listening", "previous_state": "tts-speaking", "active_tts_provider": "rime", "latency_ms": 3.42, "interruption_count": 1}
{"timestamp": "2026-09-06T07:55:18.882000+00:00", "seq": 4, "run_id": "run-a1b2c3d4", "stage": "speech-start", "state": "listening", "previous_state": "listening", "active_tts_provider": "rime"}
{"timestamp": "2026-09-06T07:55:18.883000+00:00", "seq": 5, "run_id": "run-a1b2c3d4", "stage": "speech-end", "state": "idle", "previous_state": "listening", "active_tts_provider": "rime"}
```

---

## Running Automated Tests & Preflight Verification

Run the full end-to-end verification and preflight check suite:
```bash
python -m unittest discover tests -v
```

All 11 tests pass:
1. `test_rime_live_catalog_and_preflight_check`: Verifies `coda`, `celeste`, `eng`, 22050Hz, HTTP and WebSocket endpoints against Rime live catalog and plugin specifications.
2. `test_log_stage_json_format_and_timestamps`: Sequential timeline metadata (`seq`, `run_id`, `state`, ISO timestamps).
3. `test_process_stt_events_and_stdout_stream`: Live stdout transcript streaming.
4. `test_speak_test_response`: Audio frame synthesis and track delivery.
5. `test_full_round_trip_audio_verification`: Complete user speech -> stdout -> TTS response round trip.
6. `test_barge_in_interruption_and_latency_logging`: Active TTS interruption, queue flush, and cancellation latency.
7. `test_double_interrupt_in_a_row`: Consecutive interruptions without race conditions or crashes.
8. `test_interrupt_before_tool_call_started`: Interruptions during `tool-pending` aborting tool execution.
9. `test_interrupt_after_tool_result_arrived_before_tts`: Interruption during `tool-completed` discarding tool results before TTS.
10. `test_tts_provider_switching_and_status`: Provider switching (Rime -> Deepgram) and `pipeline.status` inspection.
11. `test_full_timeline_reconstruction`: Deterministic sequential reconstruction of full runs.
