"""
LiveKit Voice Agent with Deepgram Streaming ASR, Multi-Provider TTS (Rime / Deepgram),
Edge-Case Interruption Hardening, and Timeline Logging.

- Captures incoming participant audio in a LiveKit room.
- Streams audio through Deepgram streaming STT and streams transcripts to stdout in real-time.
- Synthesizes and plays back speech via configurable TTS provider (official Rime TTS or Deepgram TTS).
- State-machine driven pipeline: IDLE -> LISTENING -> TOOL_PENDING -> TOOL_RUNNING -> TOOL_COMPLETED -> TTS_SPEAKING.
- Hardened barge-in: handles double interrupts, pre-tool interrupts, post-tool/pre-TTS interrupts.
- Full timeline reconstruction: monotonically increasing sequence numbers (seq), run_id, state transitions, and ISO-8601 UTC timestamps.
- TTS provider switching with runtime fallback and status exposure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Awaitable, Callable, Coroutine, Optional, cast
import uuid

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents import stt, tts
from livekit.plugins import deepgram, rime

from orchestrator import (
    SessionStateManager,
    call_backend_tool,
    format_spoken_response,
    generate_spoken_response,
    orchestrate,
)

load_dotenv()

# Setup standard logger
logger = logging.getLogger("snapback-voice-agent")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    logger.addHandler(handler)

DEFAULT_HARDCODED_SENTENCE = (
    "Hello! I received your voice audio clearly, and this is a test response synthesized by Rime Text to Speech."
)


def _patch_rime_chunk_alignment() -> None:
    """Ensure Rime HTTP chunked stream buffers odd bytes to maintain strict 16-bit PCM sample alignment."""
    try:
        from livekit.plugins.rime import tts as rime_tts

        if getattr(rime_tts.ChunkedStream, "_even_byte_patched", False):
            return

        orig_run = rime_tts.ChunkedStream._run

        async def _safe_run(self: Any, output_emitter: Any) -> None:
            orig_push = output_emitter.push
            remainder = b""

            def _aligned_push(data: bytes | bytearray) -> None:
                nonlocal remainder
                bdata = bytes(data)
                if remainder:
                    bdata = remainder + bdata
                    remainder = b""
                if len(bdata) % 2 != 0:
                    remainder = bdata[-1:]
                    bdata = bdata[:-1]
                if bdata:
                    orig_push(bdata)

            output_emitter.push = _aligned_push
            try:
                await orig_run(self, output_emitter)
            finally:
                if remainder:
                    orig_push(remainder + b"\x00")

        rime_tts.ChunkedStream._run = _safe_run
        rime_tts.ChunkedStream._even_byte_patched = True
    except Exception as e:
        logger.debug(f"Could not apply Rime chunk alignment patch: {e}")


_patch_rime_chunk_alignment()


class PipelineState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TOOL_PENDING = "tool-pending"
    TOOL_RUNNING = "tool-running"
    TOOL_COMPLETED = "tool-completed"
    TTS_SPEAKING = "tts-speaking"


class StructuredTimelineLogger:
    """
    Structured timeline logger ensuring deterministic timeline reconstruction.
    Includes seq, run_id, state, previous_state, active_tts_provider, and ISO timestamps.
    """

    def __init__(self, run_id: Optional[str] = None, default_log_path: Optional[str] = None) -> None:
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self._seq = 0
        self.default_log_path = default_log_path

    @property
    def current_seq(self) -> int:
        return self._seq

    def log(
        self,
        stage: str,
        state: Optional[PipelineState | str] = None,
        previous_state: Optional[PipelineState | str] = None,
        active_tts_provider: Optional[str] = None,
        log_file_override: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._seq += 1
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "seq": self._seq,
            "run_id": self.run_id,
            "stage": stage,
            "state": state.value if isinstance(state, PipelineState) else (state or "idle"),
            "previous_state": (
                previous_state.value if isinstance(previous_state, PipelineState) else previous_state
            ),
            "active_tts_provider": active_tts_provider or os.getenv("TTS_PROVIDER", "rime"),
            **kwargs,
        }
        line = json.dumps(record) + "\n"

        paths_to_write: list[Path] = []
        if log_file_override:
            paths_to_write.append(Path(log_file_override))
        elif self.default_log_path:
            paths_to_write.append(Path(self.default_log_path))
        else:
            env_log_path = os.getenv("LOG_FILE_PATH")
            if env_log_path:
                paths_to_write.append(Path(env_log_path))
            paths_to_write.append(Path("logs/agent.log"))
            try:
                paths_to_write.append(Path("/logs/agent.log"))
            except Exception:
                pass

        written_paths: set[str] = set()
        for p in paths_to_write:
            try:
                resolved_key = str(p.resolve())
            except Exception:
                resolved_key = str(p)
            if resolved_key in written_paths:
                continue
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(line)
                written_paths.add(resolved_key)
            except Exception as err:
                logger.debug(f"Could not write stage log to {p}: {err}")

        return record


# Global singleton logger instance for top-level usage
global_timeline_logger = StructuredTimelineLogger()


def log_stage(
    stage: str,
    log_file_override: Optional[str] = None,
    state: Optional[PipelineState | str] = None,
    previous_state: Optional[PipelineState | str] = None,
    active_tts_provider: Optional[str] = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Top-level convenience logger maintaining backward compatibility while injecting timeline metadata."""
    return global_timeline_logger.log(
        stage=stage,
        state=state,
        previous_state=previous_state,
        active_tts_provider=active_tts_provider,
        log_file_override=log_file_override,
        **kwargs,
    )


def create_tts_provider(
    provider_name: Optional[str] = None,
    fallback_provider: Optional[str] = None,
    **kwargs: Any,
) -> tuple[tts.TTS, str, int]:
    """
    Factory function to instantiate configured TTS provider ('rime' or 'deepgram') with fallback support.
    Returns (tts_instance, active_provider_name, sample_rate).
    """
    selected = (provider_name or os.getenv("TTS_PROVIDER", "rime")).lower().strip()
    fallback = (fallback_provider or os.getenv("TTS_FALLBACK_PROVIDER", "deepgram")).lower().strip()
    api_key = kwargs.pop("api_key", None)

    try:
        if selected == "rime":
            model = kwargs.pop("model", os.getenv("RIME_MODEL", "coda"))
            speaker = kwargs.pop("speaker", os.getenv("RIME_VOICE", os.getenv("RIME_SPEAKER", "celeste")))
            sample_rate = kwargs.pop("sample_rate", int(os.getenv("RIME_SAMPLE_RATE", "22050")))
            lang = kwargs.pop("lang", os.getenv("RIME_LANG", "eng"))
            use_ws = kwargs.pop(
                "use_websocket",
                os.getenv("RIME_USE_WEBSOCKET", "false").lower() in ("true", "1", "yes"),
            )
            key = api_key or os.getenv("RIME_API_KEY") or "test_rime_key"
            return (
                rime.TTS(
                    model=model,
                    speaker=speaker,
                    lang=lang,
                    sample_rate=sample_rate,
                    use_websocket=use_ws,
                    api_key=key,
                    **kwargs,
                ),
                "rime",
                sample_rate,
            )
        elif selected == "deepgram":
            model = kwargs.pop("model", os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-andromeda-en"))
            sample_rate = kwargs.pop("sample_rate", int(os.getenv("DEEPGRAM_TTS_SAMPLE_RATE", "24000")))
            key = api_key or os.getenv("DEEPGRAM_API_KEY") or "test_deepgram_key"
            return deepgram.TTS(model=model, sample_rate=sample_rate, api_key=key, **kwargs), "deepgram", sample_rate
        else:
            raise ValueError(f"Unsupported TTS provider: {selected}")
    except Exception as exc:
        logger.warning(
            f"Failed to initialize requested TTS provider '{selected}': {exc}. Attempting fallback to '{fallback}'."
        )
        if fallback == "deepgram" and selected != "deepgram":
            model = kwargs.pop("model", os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-andromeda-en"))
            sample_rate = kwargs.pop("sample_rate", int(os.getenv("DEEPGRAM_TTS_SAMPLE_RATE", "24000")))
            key = api_key or os.getenv("DEEPGRAM_API_KEY") or "test_deepgram_key"
            return deepgram.TTS(model=model, sample_rate=sample_rate, api_key=key, **kwargs), "deepgram", sample_rate
        elif fallback == "rime" and selected != "rime":
            model = kwargs.pop("model", os.getenv("RIME_MODEL", "coda"))
            speaker = kwargs.pop("speaker", os.getenv("RIME_VOICE", os.getenv("RIME_SPEAKER", "celeste")))
            sample_rate = kwargs.pop("sample_rate", int(os.getenv("RIME_SAMPLE_RATE", "22050")))
            lang = kwargs.pop("lang", os.getenv("RIME_LANG", "eng"))
            use_ws = kwargs.pop(
                "use_websocket",
                os.getenv("RIME_USE_WEBSOCKET", "false").lower() in ("true", "1", "yes"),
            )
            key = api_key or os.getenv("RIME_API_KEY") or "test_rime_key"
            return (
                rime.TTS(
                    model=model,
                    speaker=speaker,
                    lang=lang,
                    sample_rate=sample_rate,
                    use_websocket=use_ws,
                    api_key=key,
                    **kwargs,
                ),
                "rime",
                sample_rate,
            )
        raise


class VoiceAudioPipeline:
    """
    Manages the direct audio round-trip with edge-case hardened barge-in interruption:
    Audio Inbound -> Deepgram Streaming ASR -> stdout transcript -> Tool/Response -> TTS -> Audio Outbound.
    """

    def __init__(
        self,
        audio_source: rtc.AudioSource,
        tts_instance: Optional[tts.TTS] = None,
        active_tts_provider: Optional[str] = None,
        test_sentence: Optional[str] = None,
        log_file_override: Optional[str] = None,
        tool_executor: Optional[Callable[[str], Awaitable[str]]] = None,
        run_id: Optional[str] = None,
        session: Optional[SessionStateManager] = None,
        on_transition: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
        on_interrupt: Optional[Callable[[], Awaitable[None]]] = None,
        on_transcript: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ) -> None:
        self.audio_source = audio_source
        self.active_tts_provider = active_tts_provider or os.getenv("TTS_PROVIDER", "rime")
        if tts_instance is not None:
            self.tts = tts_instance
        else:
            self.tts, self.active_tts_provider, _ = create_tts_provider(self.active_tts_provider)

        # Backwards compatibility alias for rime_tts attribute
        self.rime_tts = self.tts

        self.test_sentence = test_sentence or os.getenv("TEST_SENTENCE", DEFAULT_HARDCODED_SENTENCE)
        self.log_file_override = log_file_override
        self.tool_executor = tool_executor
        self.on_transition = on_transition
        self.on_interrupt = on_interrupt
        self.on_transcript = on_transcript

        self.logger = StructuredTimelineLogger(run_id=run_id, default_log_path=log_file_override)
        self._session = session
        if self._session:
            self._session.run_id = self.logger.run_id
            if not self._session._log_file:
                self._session._log_file = Path(self.logger.default_log_path) if self.logger.default_log_path else Path(os.getenv("LOG_FILE_PATH", "logs/agent.log"))

        # State tracking
        self.state: PipelineState = PipelineState.IDLE
        self.is_speaking = False
        self.is_tts_active = False
        self.interruption_count = 0

        # Tasks and concurrency management
        self._current_response_task: Optional[asyncio.Task] = None
        self._fallback_turn_task: Optional[asyncio.Task] = None
        self._cancel_lock = asyncio.Lock()
        self._final_transcript_event = asyncio.Event()
        self._current_utterance_transcript: Optional[str] = None
        self._last_final_transcript: Optional[str] = None

    @property
    def status(self) -> dict[str, Any]:
        """Expose active status, provider, states, and counters."""
        return {
            "state": self.state.value,
            "active_tts_provider": self.active_tts_provider,
            "is_speaking": self.is_speaking,
            "is_tts_active": self.is_tts_active,
            "is_tool_running": self.state == PipelineState.TOOL_RUNNING,
            "interruption_count": self.interruption_count,
            "run_id": self.logger.run_id,
            "seq": self.logger.current_seq,
        }

    def switch_tts_provider(
        self,
        provider: str | tts.TTS,
        active_provider_name: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Switch active TTS provider dynamically at runtime."""
        if isinstance(provider, str):
            new_tts, active_name, _ = create_tts_provider(provider, **kwargs)
        else:
            new_tts = provider
            active_name = active_provider_name or getattr(provider, "provider", "custom")

        old_provider = self.active_tts_provider
        self.tts = new_tts
        self.rime_tts = new_tts
        self.active_tts_provider = active_name
        self.logger.log(
            stage="tts-provider-switched",
            state=self.state,
            previous_state=self.state,
            active_tts_provider=self.active_tts_provider,
            previous_provider=old_provider,
            log_file_override=self.log_file_override,
        )

    def _transition_to(self, new_state: PipelineState, stage: str, **kwargs: Any) -> dict[str, Any]:
        """Transition pipeline state and record in structured timeline log."""
        prev = self.state
        self.state = new_state
        record = self.logger.log(
            stage=stage,
            state=new_state,
            previous_state=prev,
            active_tts_provider=self.active_tts_provider,
            log_file_override=self.log_file_override,
            **kwargs,
        )
        if self.on_transition:
            self._schedule_callback(self.on_transition(stage, prev.value, new_state.value))
        return record

    def _schedule_callback(self, coro: Awaitable[None]) -> None:
        """Schedule an async callback without awaiting; swallows errors in logs."""
        try:
            loop = asyncio.get_running_loop()
            asyncio.ensure_future(coro, loop=loop)
        except Exception:  # noqa: BLE001
            logger.debug("Could not schedule pipeline callback")

    async def _fire_interrupt(self) -> None:
        """Fire the interrupt callback if registered."""
        if self.on_interrupt:
            await self.on_interrupt()

    async def cancel_active(self, participant_id: Optional[str] = None) -> Optional[float]:
        """
        Idempotent and re-entrant hardened cancellation handler:
        - Handles double interrupts in rapid succession without race conditions.
        - Handles interruptions before any tool call started (TOOL_PENDING).
        - Handles interruptions after a tool result arrived but before TTS starts (TOOL_COMPLETED).
        - Handles interruptions during active TTS playback (TTS_SPEAKING).
        """
        async with self._cancel_lock:
            self.interruption_count += 1
            t0 = time.perf_counter()
            interrupted_state = self.state

            # Record interrupt detection
            self.logger.log(
                stage="interrupt-detected",
                state=self.state,
                previous_state=self.state,
                active_tts_provider=self.active_tts_provider,
                participant_id=participant_id,
                interrupted_state=interrupted_state.value,
                interruption_count=self.interruption_count,
                log_file_override=self.log_file_override,
            )

            if self.on_interrupt:
                self._schedule_callback(self.on_interrupt())

            # Cancel in-flight response/tool/TTS task
            task_to_cancel = self._current_response_task
            if task_to_cancel and not task_to_cancel.done():
                task_to_cancel.cancel()

            # Always flush queued audio buffer immediately
            if hasattr(self.audio_source, "clear_queue"):
                try:
                    self.audio_source.clear_queue()
                except Exception as e:
                    logger.debug(f"Error clearing AudioSource queue: {e}")

            if self._fallback_turn_task and not self._fallback_turn_task.done():
                self._fallback_turn_task.cancel()

            if task_to_cancel:
                await asyncio.gather(task_to_cancel, return_exceptions=True)

            self._current_response_task = None
            self.is_tts_active = False

            if self._session:
                # Force invalidate any in-flight tools with a new interrupt ID
                self._session.issue_request(f"interrupt-{uuid.uuid4().hex[:8]}")

            t1 = time.perf_counter()
            latency_ms = (t1 - t0) * 1000.0

            # Log appropriate cancellation confirmation based on interrupted state
            if interrupted_state in (PipelineState.TOOL_PENDING, PipelineState.TOOL_RUNNING):
                self._transition_to(
                    PipelineState.LISTENING,
                    "tool-cancelled",
                    participant_id=participant_id,
                    interrupted_state=interrupted_state.value,
                    latency_ms=round(latency_ms, 2),
                    interruption_count=self.interruption_count,
                )
            else:
                self._transition_to(
                    PipelineState.LISTENING,
                    "tts-cancelled",
                    participant_id=participant_id,
                    interrupted_state=interrupted_state.value,
                    latency_ms=round(latency_ms, 2),
                    interruption_count=self.interruption_count,
                )

            logger.info(
                f"Barge-in: Interrupted state '{interrupted_state.value}' resolved in {latency_ms:.2f}ms "
                f"(count={self.interruption_count})"
            )
            return latency_ms

    # Backwards compatibility alias
    async def cancel_active_tts(self, participant_id: Optional[str] = None) -> Optional[float]:
        return await self.cancel_active(participant_id=participant_id)

    async def _execute_turn_response(
        self,
        participant_id: Optional[str] = None,
        transcript: str = "",
        request_id: Optional[str] = None,
    ) -> None:
        """
        Executes turn lifecycle:
        TOOL_PENDING -> (TOOL_RUNNING -> TOOL_COMPLETED if tool_executor present) -> TTS_SPEAKING -> IDLE.
        """
        try:
            response_text = self.test_sentence

            # Execute tool if configured
            if self.tool_executor is not None:
                self._transition_to(
                    PipelineState.TOOL_PENDING,
                    "tool-pending",
                    participant_id=participant_id,
                    transcript=transcript,
                    request_id=request_id,
                )
                # Yield control briefly so a rapid interrupt before tool execution can trigger cleanly
                await asyncio.sleep(0.01)

                self._transition_to(
                    PipelineState.TOOL_RUNNING,
                    "tool-start",
                    participant_id=participant_id,
                    transcript=transcript,
                    request_id=request_id,
                )
                tool_result = await self.tool_executor(transcript)

                self._transition_to(
                    PipelineState.TOOL_COMPLETED,
                    "tool-result",
                    participant_id=participant_id,
                    result=tool_result,
                    request_id=request_id,
                )
                
                if self._session is not None and request_id is not None:
                    # Single explicit fencing gate through orchestrator logic
                    fenced_result = self._session.resolve(request_id, tool_result)
                    if fenced_result is None:
                        # Session already logged the stale-result-discarded event
                        self._transition_to(
                            PipelineState.LISTENING,
                            "tool-result-stale",
                            participant_id=participant_id,
                            request_id=request_id,
                        )
                        return
                    tool_result = fenced_result

                response_text = tool_result
                # Yield control so an interrupt after tool result but before TTS speaking can trigger cleanly
                await asyncio.sleep(0.05)

            # Synthesize and stream TTS
            self._transition_to(
                PipelineState.TTS_SPEAKING,
                "tts-start",
                participant_id=participant_id,
                text=response_text,
                tts_provider=self.active_tts_provider,
                request_id=request_id,
            )
            self.is_tts_active = True
            if self.on_transcript and response_text:
                try:
                    await self.on_transcript("agent", response_text)
                except Exception as e:
                    logger.debug(f"Error publishing agent transcript: {e}")

            synth_stream = self.tts.synthesize(response_text)
            frame_count = 0
            playback_start = time.perf_counter()
            queued_audio_duration = 0.0

            async for audio_chunk in synth_stream:
                if hasattr(audio_chunk, "frame") and audio_chunk.frame is not None:
                    frame = audio_chunk.frame
                    await self.audio_source.capture_frame(frame)
                    frame_count += 1
                    frame_dur = getattr(frame, "duration", None)
                    if isinstance(frame_dur, (int, float)) and frame_dur > 0:
                        queued_audio_duration += frame_dur

            # Maintain active TTS_SPEAKING state until all queued audio frames finish playing out through WebRTC
            if hasattr(self.audio_source, "wait_for_playout"):
                res = self.audio_source.wait_for_playout()
                if asyncio.iscoroutine(res):
                    await res
            elif queued_audio_duration > 0:
                await asyncio.sleep(queued_audio_duration)

            self.is_tts_active = False
            self._transition_to(
                PipelineState.IDLE,
                "tts-end",
                participant_id=participant_id,
                text=response_text,
                frame_count=frame_count,
                tts_provider=self.active_tts_provider,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            self.is_tts_active = False
            logger.info(f"Turn response task cancelled while in state '{self.state.value}'")
            raise
        except Exception as e:
            self.is_tts_active = False
            self._transition_to(
                PipelineState.IDLE,
                "error",
                participant_id=participant_id,
                error=str(e),
                request_id=request_id,
            )
            logger.error(f"Error during turn response execution: {e}")

    async def speak_test_response(self, participant_id: Optional[str] = None, transcript: str = "", request_id: Optional[str] = None) -> None:
        """Direct invocation helper that executes turn response and awaits completion."""
        await self._execute_turn_response(participant_id=participant_id, transcript=transcript, request_id=request_id)

    async def wait_for_tts(self) -> None:
        """Wait for the active response / TTS task to finish if executing."""
        if self._current_response_task and not self._current_response_task.done():
            try:
                await self._current_response_task
            except asyncio.CancelledError:
                pass

    async def _fallback_turn_trigger(self, participant_id: Optional[str] = None) -> None:
        """Fallback turn trigger if Deepgram VAD endpointing does not deliver END_OF_SPEECH."""
        try:
            await asyncio.sleep(0.75)
            if self.is_speaking and self._current_utterance_transcript and self._current_utterance_transcript.strip():
                logger.info("Fallback turn debounce timer triggered; concluding turn.")
                await self._conclude_speech_turn(participant_id=participant_id)
        except asyncio.CancelledError:
            pass

    async def _conclude_speech_turn(self, participant_id: Optional[str] = None) -> None:
        """Conclude speech turn safely and trigger response execution."""
        if self._fallback_turn_task and not self._fallback_turn_task.done():
            self._fallback_turn_task.cancel()

        if not self.is_speaking and not getattr(self, "_current_utterance_transcript", None):
            return

        self.is_speaking = False

        # If final transcript hasn't arrived yet from Deepgram, give it a grace window
        if not getattr(self, "_current_utterance_transcript", None):
            try:
                await asyncio.wait_for(self._final_transcript_event.wait(), timeout=0.6)
            except asyncio.TimeoutError:
                pass

        # Strictly require a non-empty final transcript from the current utterance.
        current_tx = getattr(self, "_current_utterance_transcript", None)
        self._current_utterance_transcript = None

        if not current_tx or not current_tx.strip():
            logger.debug("Speech ended without a final transcript for this utterance; skipping turn execution.")
            # If agent is currently speaking, preserve TTS_SPEAKING state so voice playback does not break
            if not (self.is_tts_active or self.state == PipelineState.TTS_SPEAKING):
                self._transition_to(
                    PipelineState.IDLE,
                    "speech-end",
                    participant_id=participant_id,
                )
            return

        # Only cancel prior response task now that we have a valid, new non-empty utterance
        if self._current_response_task and not self._current_response_task.done():
            self._current_response_task.cancel()

        self._transition_to(
            PipelineState.IDLE,
            "speech-end",
            participant_id=participant_id,
        )

        request_id = f"utt-{uuid.uuid4().hex[:12]}"
        if self._session:
            request_id = self._session.issue_request(request_id)

        # Launch managed turn response asynchronously
        self._current_response_task = asyncio.create_task(
            self._execute_turn_response(
                participant_id=participant_id,
                transcript=current_tx,
                request_id=request_id,
            )
        )

    async def process_stt_events(
        self,
        stt_stream: stt.RecognizeStream,
        participant_id: Optional[str] = None,
    ) -> None:
        """
        Process streaming STT events from Deepgram, detect barge-in interruptions,
        stream transcripts to stdout, and trigger response on speech completion.
        """
        async for event in stt_stream:
            if event.type == stt.SpeechEventType.START_OF_SPEECH:
                # When agent is speaking, raw VAD energy occurs from speaker output.
                # We do not cut off the agent on raw energy alone; genuine barge-in
                # is triggered as soon as words arrive in INTERIM_TRANSCRIPT.
                if self.state == PipelineState.TTS_SPEAKING or self.is_tts_active:
                    continue

                if not self.is_speaking:
                    self.is_speaking = True
                    self._transition_to(
                        PipelineState.LISTENING,
                        "speech-start",
                        participant_id=participant_id,
                    )
                    # Only reset per-utterance transcript accumulator when beginning a new utterance
                    self._current_utterance_transcript = None
                    self._final_transcript_event.clear()

            elif event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                alt_texts = [alt.text for alt in event.alternatives if alt.text]
                if alt_texts:
                    interim = " ".join(alt_texts).strip()
                    # Cancel any active fallback turn timer since speech is continuing
                    if self._fallback_turn_task and not self._fallback_turn_task.done():
                        self._fallback_turn_task.cancel()

                    # If actual spoken words arrive while a tool or TTS playback is in-flight,
                    # immediately cancel active playback (<50ms) as a genuine voice barge-in.
                    if interim:
                        if self.state in (
                            PipelineState.TTS_SPEAKING,
                            PipelineState.TOOL_PENDING,
                            PipelineState.TOOL_RUNNING,
                            PipelineState.TOOL_COMPLETED,
                        ) or (self._current_response_task and not self._current_response_task.done()):
                            await self.cancel_active(participant_id=participant_id)

                        if not self.is_speaking:
                            self.is_speaking = True
                            self._transition_to(
                                PipelineState.LISTENING,
                                "speech-start",
                                participant_id=participant_id,
                            )

                        sys.stdout.write(f"\r[Deepgram ASR Interim] {interim}")
                        sys.stdout.flush()
                        if self.on_transcript:
                            await self.on_transcript("user", interim)

            elif event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                alt_texts = [alt.text for alt in event.alternatives if alt.text]
                if alt_texts:
                    final_text = " ".join(alt_texts).strip()
                    if final_text:
                        # If interim was skipped or single-word final arrived during active TTS/tool, cancel active
                        if self.state in (
                            PipelineState.TTS_SPEAKING,
                            PipelineState.TOOL_PENDING,
                            PipelineState.TOOL_RUNNING,
                            PipelineState.TOOL_COMPLETED,
                        ) or (self._current_response_task and not self._current_response_task.done()):
                            await self.cancel_active(participant_id=participant_id)

                        if not self.is_speaking:
                            self.is_speaking = True
                            self._transition_to(
                                PipelineState.LISTENING,
                                "speech-start",
                                participant_id=participant_id,
                            )

                        if self._current_utterance_transcript:
                            self._current_utterance_transcript = f"{self._current_utterance_transcript} {final_text}".strip()
                        else:
                            self._current_utterance_transcript = final_text
                        self._last_final_transcript = self._current_utterance_transcript
                        self._final_transcript_event.set()
                        sys.stdout.write(f"\n[Deepgram ASR Final] {self._current_utterance_transcript}\n")
                        sys.stdout.flush()
                        if self.on_transcript:
                            await self.on_transcript("user", final_text)

                        # Schedule fallback turn timer (750ms) in case Deepgram VAD doesn't emit END_OF_SPEECH
                        if self._fallback_turn_task and not self._fallback_turn_task.done():
                            self._fallback_turn_task.cancel()
                        self._fallback_turn_task = asyncio.create_task(
                            self._fallback_turn_trigger(participant_id=participant_id)
                        )

            elif event.type == stt.SpeechEventType.END_OF_SPEECH:
                await self._conclude_speech_turn(participant_id=participant_id)

    async def handle_participant_track(
        self,
        track: rtc.Track,
        participant: rtc.RemoteParticipant,
        stt_instance: Optional[stt.STT] = None,
    ) -> None:
        """Attach to participant audio track and stream frames into Deepgram STT with auto-reconnect resilience."""
        audio_stream = rtc.AudioStream(track)

        while not asyncio.current_task().cancelled():
            try:
                dg_stt = stt_instance or deepgram.STT(
                    model=os.getenv("DEEPGRAM_MODEL", "nova-3"),
                    language="en-US",
                    vad_events=True,
                    interim_results=True,
                    punctuate=True,
                    endpointing_ms=300,
                    utterance_end_ms=1000,
                )
                stt_stream = dg_stt.stream()

                async def _forward_audio() -> None:
                    try:
                        async for event in audio_stream:
                            stt_stream.push_frame(event.frame)
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Error forwarding audio frames: {e}")
                    finally:
                        try:
                            stt_stream.end_input()
                        except Exception:
                            pass

                forward_task = asyncio.create_task(_forward_audio())
                try:
                    await self.process_stt_events(stt_stream, participant_id=participant.identity)
                finally:
                    forward_task.cancel()
                    try:
                        await stt_stream.aclose()
                    except Exception:
                        pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"STT stream exception for participant {participant.identity}: {exc}. Reconnecting STT in 500ms...")
                await asyncio.sleep(0.5)

            if stt_instance is not None:
                break


async def run_agent_in_room(room: rtc.Room) -> VoiceAudioPipeline:
    """Initialize and run the voice audio pipeline inside any connected rtc.Room."""
    # Initialize TTS provider: use 24000Hz for WebRTC audio to prevent frame drops and Opus clock drift
    desired_sample_rate = int(os.getenv("TTS_SAMPLE_RATE", "24000"))
    tts_instance, active_provider, tts_sample_rate = create_tts_provider(sample_rate=desired_sample_rate)
    logger.info(f"Initialized active TTS provider: '{active_provider}' at {tts_sample_rate}Hz")

    audio_source = rtc.AudioSource(sample_rate=tts_sample_rate, num_channels=1)
    agent_audio_track = rtc.LocalAudioTrack.create_audio_track("agent_voice", audio_source)

    pub_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await room.local_participant.publish_track(agent_audio_track, pub_options)
    logger.info("Published agent audio track to room")

    session = SessionStateManager(log_file=os.getenv("LOG_FILE_PATH", "logs/agent.log"))

    async def tool_executor(transcript: str) -> str:
        try:
            req_id = session.current_request_id
            intent = await orchestrate(transcript, session=session, utterance_id=req_id)
            if isinstance(intent, dict):
                backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
                res = await call_backend_tool(intent, base_url=backend_url, request_id=req_id)
                spoken = await generate_spoken_response(
                    intent.get("tool", ""),
                    intent.get("args", {}),
                    res,
                    user_transcript=transcript,
                )
                return spoken
            return intent
        except Exception as err:
            logger.error(f"Error in tool_executor: {err}")
            return f"I encountered an issue: {err}"

    pipeline = VoiceAudioPipeline(
        audio_source=audio_source,
        tts_instance=tts_instance,
        active_tts_provider=active_provider,
        session=session,
        tool_executor=tool_executor,
    )

    _PIPELINE_TO_UI_STATE: dict[PipelineState, str] = {
        PipelineState.IDLE: "idle",
        PipelineState.LISTENING: "listening",
        PipelineState.TOOL_PENDING: "listening",
        PipelineState.TOOL_RUNNING: "listening",
        PipelineState.TOOL_COMPLETED: "listening",
        PipelineState.TTS_SPEAKING: "speaking",
    }

    async def send_data_message(payload: dict[str, Any]) -> None:
        try:
            await room.local_participant.publish_data(
                payload=json.dumps(payload).encode("utf-8"),
                topic="snapback",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Could not publish data message: {e}")

    async def on_transition(stage: str, previous_state: str, new_state: str) -> None:
        for pipeline_state, ui_state in _PIPELINE_TO_UI_STATE.items():
            if pipeline_state.value == new_state:
                await send_data_message(
                    {
                        "type": "state_update",
                        "state": ui_state,
                        "stage": stage,
                        "previous_state": previous_state,
                    }
                )
                break

    async def on_interrupt() -> None:
        await send_data_message({"type": "state_update", "state": "interrupted"})

    async def on_transcript(speaker: str, text: str) -> None:
        await send_data_message({"type": "transcript", "speaker": speaker, "text": text})

    pipeline.on_transition = on_transition
    pipeline.on_interrupt = on_interrupt
    pipeline.on_transcript = on_transcript

    async def handle_external_utterance(text: str) -> None:
        await pipeline.cancel_active()
        if pipeline.on_transcript:
            await pipeline.on_transcript("user", text)
        req_id = f"utt-{uuid.uuid4().hex[:12]}"
        if session:
            req_id = session.issue_request(req_id)
        pipeline._current_response_task = asyncio.create_task(
            pipeline._execute_turn_response(transcript=text, request_id=req_id)
        )

    @room.on("data_received")
    def on_data_received(data_packet: Any) -> None:
        try:
            raw = getattr(data_packet, "data", data_packet)
            msg = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw))
            if msg.get("type") in ("user_utterance", "barge_in"):
                text = msg.get("text", "")
                if text:
                    asyncio.create_task(handle_external_utterance(text))
        except Exception as err:
            logger.debug(f"Error handling data packet: {err}")

    active_participant_tasks: dict[str, asyncio.Task] = {}

    def cleanup_participant(identity: str) -> None:
        task = active_participant_tasks.pop(identity, None)
        if task and not task.done():
            logger.info(f"Cancelling audio stream for participant {identity}")
            task.cancel()
        pipeline.is_speaking = False
        pipeline._current_utterance_transcript = None
        if pipeline._fallback_turn_task and not pipeline._fallback_turn_task.done():
            pipeline._fallback_turn_task.cancel()
        if pipeline.state in (PipelineState.LISTENING, PipelineState.TOOL_PENDING):
            pipeline._transition_to(PipelineState.IDLE, "participant-reset")

    def subscribe_track(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            curr = active_participant_tasks.get(participant.identity)
            if curr and not curr.done():
                return

            for old_id in list(active_participant_tasks.keys()):
                if old_id != participant.identity:
                    logger.info(f"Cleaning up prior participant {old_id} before attaching {participant.identity}")
                    cleanup_participant(old_id)

            cleanup_participant(participant.identity)
            logger.info(f"Subscribing to audio track from participant {participant.identity}")
            task = asyncio.create_task(pipeline.handle_participant_track(track, participant))
            active_participant_tasks[participant.identity] = task

            def _cleanup(t: asyncio.Task) -> None:
                if t.cancelled():
                    logger.debug(f"Audio stream task for participant {participant.identity} cancelled")
                elif t.exception():
                    logger.error(f"Audio stream task for participant {participant.identity} failed: {t.exception()}", exc_info=t.exception())
                if active_participant_tasks.get(participant.identity) is t:
                    active_participant_tasks.pop(participant.identity, None)

            task.add_done_callback(_cleanup)

    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        subscribe_track(track, participant)

    @room.on("track_unsubscribed")
    def on_track_unsubscribed(
        track: rtc.Track,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        logger.info(f"Track unsubscribed for participant {participant.identity}")
        cleanup_participant(participant.identity)

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        logger.info(f"Participant disconnected: {participant.identity}")
        cleanup_participant(participant.identity)

    for participant in room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                subscribe_track(publication.track, participant)

    logger.info(f"Voice agent ready (active_tts_provider={pipeline.active_tts_provider}).")
    return pipeline


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit agent entrypoint."""
    logger.info("Starting voice agent worker, connecting to LiveKit room...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    @ctx.room.on("disconnected")
    def on_disconnected(reason: Any = None) -> None:
        logger.info(f"Room disconnected ({reason}). Exiting process.")
        os._exit(0)

    await run_agent_in_room(ctx.room)



def main() -> None:
    """Run the worker CLI."""
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
        )
    )


if __name__ == "__main__":
    main()
