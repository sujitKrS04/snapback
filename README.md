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

## Running the Full Stack

### 1. Start the Booking Backend (FastAPI)
The backend provides scheduling tools (`/check-availability`, `/book`), LiveKit token generation (`/token`), and SSE telemetry (`/events`):
```bash
uvicorn backend:app --host 127.0.0.1 --port 8000
```

### 2. Start the Voice Agent Runner
Runs the persistent voice agent connected to the LiveKit room:
```bash
python run_agent.py
```
*(Or run in local development agent worker mode: `python agent.py dev`)*

### 3. Start the Snapback Studio Frontend
In a new terminal, launch the modern React + Vite Studio:
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173/` in your browser to start a full-duplex conversational voice call!

---

## Snapback Studio & Audio Architecture Hardening

### 1. Snapback Studio Interface
- **Acoustic Resonator**: 3D interactive orb with real-time waveform reactions to user mic input and agent speech.
- **Dark & Light Themes**: High-contrast tactile design system with smooth animated theme transitions.
- **Live Dialogue Stream**: Chronological turn stream showing live speech transcripts and agent responses.
- **Live Telemetry & SSE**: Non-blocking server-sent event tailing exposing sub-50ms barge-in latency measurements in real time.
- **Quick-Barge Prompt Chips**: Clickable prompt chips that transmit instant interruption data packets over the WebRTC data channel (`{ type: "barge_in", text: prompt }`).
- **Built-in Session Recording**: Capture combined canvas, screen, and audio sessions directly to WebM/MP4 format.

### 2. Audio Pipeline & Distortion Hardening
- **16-bit PCM Chunk Alignment**: When streaming raw 16-bit linear PCM (`audio/pcm`), network chunk fragmentation can yield odd byte boundaries. Snapback integrates an automatic even-byte chunk alignment buffer (`_patch_rime_chunk_alignment()`), preventing sample transposition and eliminating crackling/buzzing distortion.
- **Native Playout Synchronization**: Uses `AudioSource.wait_for_playout()` rather than manual sleep timers, ensuring that HTTP synthesis latency is not subtracted from speech duration and preventing sentences from cutting off mid-speech.
- **Fallback Turn Debounce & Utterance End**: Employs a 750ms silence debounce timer and `utterance_end_ms=1000` on Deepgram streaming STT, guaranteeing turn completion and prompt voice replies even in environments with ambient microphone noise.
- **Resilient STT Stream Loop**: Continuous reconnection supervisor in `handle_participant_track` ensuring uninterrupted speech recognition across WebSocket drops or network reconnects.
- **Singleton Process Protection**: Enforces a PID lockfile (`logs/agent.pid`) and process guards in `run_agent.py` and `backend.py`, preventing duplicate agent workers from connecting to the same room and conflicting on STT streams.
- **Dynamic Participant Lifecycle**: Automatically cleans up and cancels STT tasks on `@room.on("participant_disconnected")` and `@room.on("track_unsubscribed")`, eliminating ghost listener tasks across call reconnects.
- **DOM Audio Stacking Prevention**: Automatically manages `<audio>` element lifecycles in `useLiveKit.ts`, ensuring zero audio element duplication or comb-filter phase cancellation distortion.

---

## Live Demonstrations & Video Artifacts

Three live demonstration video recordings captured directly from the physical hardware microphone, WebRTC live room (`snapback-call`), and Snapback Studio frontend:

1. **[`live_demo_1.mp4`](live_demo_1.mp4)**: Complete conversational appointment booking flow with live Deepgram ASR, Rime Coda synthesis, and instant voice interruptions.
2. **[`live_demo_2.mp4`](live_demo_2.mp4)**: Interactive barge-in and quick-prompt chip testing demonstrating sub-50ms audio cancellations during active tool execution and speech.
3. **[`live_demo_3.mp4`](live_demo_3.mp4)**: Full-duplex conversational voice turn transitions, resilient participant reconnection, and real-time SSE latency telemetry.

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

Run the full automated test suite using `pytest`:
```bash
pytest
```
or with verbose output:
```bash
pytest -v
```

The test suite consists of **86 passing tests** with 100% pass rate across two primary test modules:

### 1. Voice Agent Pipeline & Barge-In Hardening ([`tests/test_agent_pipeline.py`](file:///d:/Projects/snapback/tests/test_agent_pipeline.py) — 17 tests)
- **Rime Live Catalog Preflight**: Validates `coda` model, `celeste` speaker, `eng` language, 22,050 Hz output, and HTTP/WebSocket transport endpoints against official Rime plugin enums.
- **Edge-Case Interruption Hardening**: Exhaustive validation of all 3 interruption lifecycle scenarios:
  - *Scenario 1*: Interruption before tool dispatch (`tool-pending` -> `tool-cancelled`).
  - *Scenario 2*: Interruption during in-flight tool execution (`tool-running` -> `tool-cancelled`).
  - *Scenario 3*: Interruption during active audio playback (`tts-speaking` -> `tts-cancelled` with instantaneous native buffer queue flush).
- **Double-Interrupt & Re-entrancy Safety**: Rapid-fire sequential barge-ins tested without race conditions, deadlocks, or task leaks.
- **Interim ASR Transcript Isolation**: Confirms that partial Deepgram speech frames (`INTERIM_TRANSCRIPT`) never trigger tool execution or turn responses.
- **Per-Utterance Accumulator Isolation**: Ensures completed transcripts from prior turns cannot leak across speech boundaries.
- **Deterministic Timeline Logging**: Validates sequential `seq` counter monotonicity, run IDs, ISO-8601 UTC timestamps, and stage transition records.

### 2. Backend, Orchestrator, Fencing & SSE Streaming ([`tests/test_backend_and_orchestrator.py`](file:///d:/Projects/snapback/tests/test_backend_and_orchestrator.py) — 69 tests)
- **FastAPI Booking Service**: Endpoint contracts, query parameters, artificial latency injection, and structured JSON access logging for `/check-availability`, `/book`, and `/health`.
- **LiveKit Room Authentication**: JWT generation and video grant validation for the `/token` endpoint.
- **Server-Sent Events (SSE) Engine**: Real-time `/events` streaming with non-blocking log tailing, padded replay framing, and live timeline event ingestion.
- **LLM Intent Orchestration**: Heuristic & OpenAI function-calling parser, intent routing, parameter extraction, and conversational fallbacks.
- **Natural Spoken Response Synthesis**: Conversion of raw backend availability JSON payloads into natural spoken sentences (`generate_spoken_response`), eliminating raw debug prefixes (`"Tool result:"`).
- **Session State Management & Request-ID Fencing**: `SessionStateManager` monotonic utterance request tracking, concurrent supersession gating, and automated stale-result discard logging.
