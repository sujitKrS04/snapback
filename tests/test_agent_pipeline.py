"""
Tests for LiveKit Voice Agent pipeline:
- Structured JSON lines logging with ISO timestamps, seq, run_id, state transitions.
- Streaming STT handling and stdout transcript streaming.
- Multi-provider TTS (Rime, Deepgram) synthesis and switching.
- Full round-trip audio cycle: speech-start -> interim/final transcript -> speech-end -> tts-start -> tts-end.
- Edge-case barge-in hardening:
  1. Active TTS interruption & latency tracking.
  2. Double interrupts in a row.
  3. Interrupt before any tool call started.
  4. Interrupt after a tool result arrived but before TTS started speaking.
- Deterministic timeline reconstruction verification.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from livekit import rtc
from livekit.agents import stt, tts

from agent import (
    PipelineState,
    StructuredTimelineLogger,
    VoiceAudioPipeline,
    create_tts_provider,
    log_stage,
)


class TestAgentPipeline(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.log_file = Path(self.temp_dir) / "agent.log"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_stage_json_format_and_timestamps(self) -> None:
        """Verify that every stage transition is logged with ISO timestamp, seq, run_id, and state as JSON lines."""
        stages = ["speech-start", "speech-end", "tts-start", "tts-end", "interrupt-detected", "tts-cancelled"]
        for stage in stages:
            log_stage(
                stage,
                log_file_override=str(self.log_file),
                participant_id="test-user-1",
                latency_ms=12.34,
                state="listening",
            )

        self.assertTrue(self.log_file.exists(), "Log file should be created")

        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        self.assertEqual(len(lines), 6, f"Expected 6 lines, got {len(lines)}")

        for i, line in enumerate(lines):
            data = json.loads(line)
            self.assertEqual(data["stage"], stages[i])
            self.assertEqual(data["participant_id"], "test-user-1")
            self.assertIn("timestamp", data)
            self.assertIn("seq", data)
            self.assertIn("run_id", data)
            self.assertIn("active_tts_provider", data)
            # Verify ISO timestamp can be parsed
            dt = datetime.fromisoformat(data["timestamp"])
            self.assertIsNotNone(dt.tzinfo)

    async def test_process_stt_events_and_stdout_stream(self) -> None:
        """Verify that STT stream prints transcripts to stdout and logs speech-start/speech-end."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()

        mock_rime_tts = MagicMock()
        mock_frame = MagicMock(spec=rtc.AudioFrame)
        synth_audio = tts.SynthesizedAudio(frame=mock_frame, request_id="req-1", is_final=True)

        async def _mock_synthesize(text: str):
            yield synth_audio

        mock_rime_tts.synthesize = MagicMock(side_effect=_mock_synthesize)

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_rime_tts,
            active_tts_provider="rime",
            test_sentence="Test test sentence",
            log_file_override=str(self.log_file),
        )

        class MockSTTStream:
            def __init__(self, events):
                self._events = events

            def __aiter__(self):
                return self._generator()

            async def _generator(self):
                for ev in self._events:
                    await asyncio.sleep(0.01)
                    yield ev

        events = [
            stt.SpeechEvent(
                type=stt.SpeechEventType.START_OF_SPEECH,
                request_id="1",
                alternatives=[],
                created_at=0.0,
            ),
            stt.SpeechEvent(
                type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                request_id="1",
                alternatives=[stt.SpeechData(text="hello", language="en")],
                created_at=0.0,
            ),
            stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id="1",
                alternatives=[stt.SpeechData(text="hello world", language="en")],
                created_at=0.0,
            ),
            stt.SpeechEvent(
                type=stt.SpeechEventType.END_OF_SPEECH,
                request_id="1",
                alternatives=[],
                created_at=0.0,
            ),
        ]

        captured_stdout = io.StringIO()
        original_stdout = sys.stdout
        try:
            sys.stdout = captured_stdout
            await pipeline.process_stt_events(MockSTTStream(events), participant_id="participant-42")
            await pipeline.wait_for_tts()
        finally:
            sys.stdout = original_stdout

        stdout_content = captured_stdout.getvalue()
        self.assertIn("hello", stdout_content)
        self.assertIn("hello world", stdout_content)

        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line.strip()) for line in f if line.strip()]

        logged_stages = [l["stage"] for l in lines]
        self.assertEqual(logged_stages, ["speech-start", "speech-end", "tts-start", "tts-end"])

    async def test_speak_test_response(self) -> None:
        """Verify that speak_test_response logs tts-start/tts-end and pushes frames to audio_source."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()

        mock_rime_tts = MagicMock()
        mock_frame_1 = MagicMock(spec=rtc.AudioFrame)
        mock_frame_2 = MagicMock(spec=rtc.AudioFrame)

        async def _mock_synth(text: str):
            yield tts.SynthesizedAudio(frame=mock_frame_1, request_id="1")
            yield tts.SynthesizedAudio(frame=mock_frame_2, request_id="1", is_final=True)

        mock_rime_tts.synthesize = MagicMock(side_effect=_mock_synth)

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_rime_tts,
            active_tts_provider="rime",
            test_sentence="Hardcoded Rime sentence",
            log_file_override=str(self.log_file),
        )

        await pipeline.speak_test_response(participant_id="user-xyz")

        self.assertEqual(mock_audio_source.capture_frame.call_count, 2)
        mock_audio_source.capture_frame.assert_any_await(mock_frame_1)
        mock_audio_source.capture_frame.assert_any_await(mock_frame_2)

        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line.strip()) for line in f if line.strip()]

        stages = [l["stage"] for l in lines]
        self.assertEqual(stages, ["tts-start", "tts-end"])
        self.assertEqual(lines[0]["text"], "Hardcoded Rime sentence")
        self.assertEqual(lines[1]["text"], "Hardcoded Rime sentence")

    async def test_full_round_trip_audio_verification(self) -> None:
        """Confirm full round-trip audio: user speaks -> transcript logs -> Rime speaks response."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()

        mock_rime_tts = MagicMock()
        mock_frame = MagicMock(spec=rtc.AudioFrame)

        async def _mock_synth(text: str):
            yield tts.SynthesizedAudio(frame=mock_frame, request_id="roundtrip")

        mock_rime_tts.synthesize = MagicMock(side_effect=_mock_synth)

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_rime_tts,
            active_tts_provider="rime",
            test_sentence="Roundtrip audio verified",
            log_file_override=str(self.log_file),
        )

        class MockStream:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.START_OF_SPEECH,
                    request_id="rt",
                    alternatives=[],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    request_id="rt",
                    alternatives=[stt.SpeechData(text="testing full round trip", language="en")],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id="rt",
                    alternatives=[stt.SpeechData(text="testing full round trip audio", language="en")],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.END_OF_SPEECH,
                    request_id="rt",
                    alternatives=[],
                    created_at=0.0,
                )

        captured_stdout = io.StringIO()
        orig_stdout = sys.stdout
        try:
            sys.stdout = captured_stdout
            await pipeline.process_stt_events(MockStream(), participant_id="roundtrip-tester")
            await pipeline.wait_for_tts()
        finally:
            sys.stdout = orig_stdout

        out = captured_stdout.getvalue()
        self.assertIn("testing full round trip audio", out)

        mock_audio_source.capture_frame.assert_awaited_once_with(mock_frame)

        with open(self.log_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        stages = [r["stage"] for r in records]
        self.assertEqual(
            stages,
            ["speech-start", "speech-end", "tts-start", "tts-end"],
            "Stages must transition in exact round-trip order",
        )

    async def test_barge_in_interruption_and_latency_logging(self) -> None:
        """Verify barge-in: user speech immediately cancels active TTS, flushes playback, and logs latency."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()
        mock_audio_source.clear_queue = MagicMock()

        mock_rime_tts = MagicMock()
        mock_frame = MagicMock(spec=rtc.AudioFrame)

        async def _mock_slow_synth(text: str):
            for i in range(20):
                await asyncio.sleep(0.04)
                yield tts.SynthesizedAudio(frame=mock_frame, request_id=f"chunk-{i}")

        mock_rime_tts.synthesize = MagicMock(side_effect=_mock_slow_synth)

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_rime_tts,
            active_tts_provider="rime",
            test_sentence="Initial TTS to be interrupted",
            log_file_override=str(self.log_file),
        )

        pipeline._current_response_task = asyncio.create_task(
            pipeline.speak_test_response(participant_id="bargein-user")
        )
        await asyncio.sleep(0.06)
        self.assertTrue(pipeline.is_tts_active, "TTS should be active before interruption")

        class InterruptionStream:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.START_OF_SPEECH,
                    request_id="bargein",
                    alternatives=[],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                    request_id="bargein",
                    alternatives=[stt.SpeechData(text="wait stop", language="en")],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id="bargein",
                    alternatives=[stt.SpeechData(text="wait stop I have a question", language="en")],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.END_OF_SPEECH,
                    request_id="bargein",
                    alternatives=[],
                    created_at=0.0,
                )

        captured_stdout = io.StringIO()
        orig_stdout = sys.stdout
        try:
            sys.stdout = captured_stdout
            await pipeline.process_stt_events(InterruptionStream(), participant_id="bargein-user")
            await pipeline.wait_for_tts()
        finally:
            sys.stdout = orig_stdout

        mock_audio_source.clear_queue.assert_called()
        self.assertIn("wait stop I have a question", captured_stdout.getvalue())

        with open(self.log_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        stages = [r["stage"] for r in records]
        self.assertIn("tts-start", stages)
        self.assertIn("interrupt-detected", stages)
        self.assertIn("tts-cancelled", stages)
        self.assertIn("speech-start", stages)
        self.assertIn("speech-end", stages)

        idx_interrupt = stages.index("interrupt-detected")
        idx_cancelled = stages.index("tts-cancelled")
        self.assertLess(idx_interrupt, idx_cancelled)

        cancelled_rec = records[idx_cancelled]
        self.assertIn("latency_ms", cancelled_rec)
        self.assertGreaterEqual(cancelled_rec["latency_ms"], 0.0)

    async def test_double_interrupt_in_a_row(self) -> None:
        """Verify hardening against double interrupts in rapid succession without race conditions or crashes."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()
        mock_audio_source.clear_queue = MagicMock()

        mock_tts = MagicMock()
        mock_frame = MagicMock(spec=rtc.AudioFrame)

        async def _mock_synth(text: str):
            for i in range(20):
                await asyncio.sleep(0.05)
                yield tts.SynthesizedAudio(frame=mock_frame, request_id=f"double-{i}")

        mock_tts.synthesize = MagicMock(side_effect=_mock_synth)

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_tts,
            active_tts_provider="rime",
            test_sentence="Sentence for double interrupt test",
            log_file_override=str(self.log_file),
        )

        # Start TTS playing
        pipeline._current_response_task = asyncio.create_task(
            pipeline.speak_test_response(participant_id="user-double")
        )
        await asyncio.sleep(0.06)
        self.assertTrue(pipeline.is_tts_active)

        # Trigger interrupt 1
        lat1 = await pipeline.cancel_active(participant_id="user-double")
        self.assertIsNotNone(lat1)
        self.assertFalse(pipeline.is_tts_active)

        # Immediately trigger interrupt 2 in a row (e.g. rapid user stammer before new TTS)
        lat2 = await pipeline.cancel_active(participant_id="user-double")
        self.assertIsNotNone(lat2)
        self.assertEqual(pipeline.interruption_count, 2)

        # Verify AudioSource clear_queue was called on both interruptions
        self.assertEqual(mock_audio_source.clear_queue.call_count, 2)

        with open(self.log_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        interrupt_stages = [r["stage"] for r in records if r["stage"] == "interrupt-detected"]
        cancel_stages = [r["stage"] for r in records if r["stage"] == "tts-cancelled"]
        self.assertEqual(len(interrupt_stages), 2)
        self.assertEqual(len(cancel_stages), 2)

    async def test_interrupt_before_tool_call_started(self) -> None:
        """Verify handling of interruption while in TOOL_PENDING state before tool starts execution."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()
        mock_audio_source.clear_queue = MagicMock()

        mock_tts = MagicMock()
        tool_executed = False

        async def _mock_tool(arg: str) -> str:
            nonlocal tool_executed
            tool_executed = True
            await asyncio.sleep(0.2)
            return "Tool finished"

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_tts,
            active_tts_provider="rime",
            log_file_override=str(self.log_file),
            tool_executor=_mock_tool,
        )

        # Start response task which enters TOOL_PENDING
        pipeline._current_response_task = asyncio.create_task(
            pipeline._execute_turn_response(participant_id="tool-user", transcript="run test")
        )
        # Check that state entered TOOL_PENDING
        for _ in range(50):
            if pipeline.state == PipelineState.TOOL_PENDING:
                break
            await asyncio.sleep(0.002)
        self.assertEqual(pipeline.state, PipelineState.TOOL_PENDING)

        # Interrupt immediately before tool starts!
        await pipeline.cancel_active(participant_id="tool-user")

        self.assertFalse(tool_executed, "Tool must NOT have executed when interrupted in TOOL_PENDING")
        self.assertEqual(pipeline.state, PipelineState.LISTENING)

        with open(self.log_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        stages = [r["stage"] for r in records]
        self.assertIn("tool-pending", stages)
        self.assertIn("interrupt-detected", stages)
        self.assertIn("tool-cancelled", stages)
        self.assertNotIn("tool-start", stages)
        self.assertNotIn("tts-start", stages)

    async def test_interrupt_after_tool_result_arrived_before_tts(self) -> None:
        """Verify handling of interruption in TOOL_COMPLETED state: tool finished, but interrupted before TTS."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()
        mock_audio_source.clear_queue = MagicMock()

        mock_tts = MagicMock()
        mock_tts.synthesize = MagicMock()

        async def _instant_tool(arg: str) -> str:
            return "42"

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_tts,
            active_tts_provider="rime",
            log_file_override=str(self.log_file),
            tool_executor=_instant_tool,
        )

        # Launch response task
        pipeline._current_response_task = asyncio.create_task(
            pipeline._execute_turn_response(participant_id="post-tool-user", transcript="calculate")
        )
        # Wait until tool finishes and enters TOOL_COMPLETED
        for _ in range(50):
            if pipeline.state == PipelineState.TOOL_COMPLETED:
                break
            await asyncio.sleep(0.003)
        self.assertEqual(pipeline.state, PipelineState.TOOL_COMPLETED)

        # Interrupt before TTS starts
        await pipeline.cancel_active(participant_id="post-tool-user")

        # TTS must never have been called
        mock_tts.synthesize.assert_not_called()
        self.assertFalse(pipeline.is_tts_active)
        self.assertEqual(pipeline.state, PipelineState.LISTENING)

        with open(self.log_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        stages = [r["stage"] for r in records]
        self.assertIn("tool-start", stages)
        self.assertIn("tool-result", stages)
        self.assertIn("interrupt-detected", stages)
        self.assertIn("tts-cancelled", stages)
        self.assertNotIn("tts-start", stages)

    def test_tts_provider_switching_and_status(self) -> None:
        """Verify switching between TTS providers (Rime/Deepgram), status exposure, and fallback visibility."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_rime_tts = MagicMock()

        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_rime_tts,
            active_tts_provider="rime",
            log_file_override=str(self.log_file),
        )

        status_init = pipeline.status
        self.assertEqual(status_init["active_tts_provider"], "rime")
        self.assertEqual(status_init["state"], "idle")
        self.assertIn("seq", status_init)
        self.assertIn("run_id", status_init)

        # Switch to Deepgram TTS provider
        pipeline.switch_tts_provider("deepgram")
        self.assertEqual(pipeline.active_tts_provider, "deepgram")
        self.assertEqual(pipeline.status["active_tts_provider"], "deepgram")

        with open(self.log_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        switch_events = [r for r in records if r["stage"] == "tts-provider-switched"]
        self.assertEqual(len(switch_events), 1)
        self.assertEqual(switch_events[0]["active_tts_provider"], "deepgram")
        self.assertEqual(switch_events[0]["previous_provider"], "rime")

    async def test_full_timeline_reconstruction(self) -> None:
        """Verify that sequential logs contain sufficient deterministic detail to reconstruct a full test timeline."""
        mock_audio_source = MagicMock(spec=rtc.AudioSource)
        mock_audio_source.capture_frame = AsyncMock()
        mock_tts = MagicMock()
        mock_frame = MagicMock(spec=rtc.AudioFrame)

        async def _mock_synth(text: str):
            yield tts.SynthesizedAudio(frame=mock_frame, request_id="timeline")

        mock_tts.synthesize = MagicMock(side_effect=_mock_synth)

        run_id = "test-reconstruction-run"
        pipeline = VoiceAudioPipeline(
            audio_source=mock_audio_source,
            tts_instance=mock_tts,
            active_tts_provider="rime",
            log_file_override=str(self.log_file),
            run_id=run_id,
        )

        class TimelineSpeechStream:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.START_OF_SPEECH,
                    request_id="tl",
                    alternatives=[],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id="tl",
                    alternatives=[stt.SpeechData(text="timeline test", language="en")],
                    created_at=0.0,
                )
                yield stt.SpeechEvent(
                    type=stt.SpeechEventType.END_OF_SPEECH,
                    request_id="tl",
                    alternatives=[],
                    created_at=0.0,
                )

        captured_stdout = io.StringIO()
        orig = sys.stdout
        try:
            sys.stdout = captured_stdout
            await pipeline.process_stt_events(TimelineSpeechStream(), participant_id="timeline-user")
            await pipeline.wait_for_tts()
        finally:
            sys.stdout = orig

        with open(self.log_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        self.assertGreaterEqual(len(records), 4)

        # Validate monotonic sequence numbers and unique run_id
        last_seq = 0
        last_ts = None
        for r in records:
            self.assertEqual(r["run_id"], run_id)
            self.assertEqual(r["active_tts_provider"], "rime")
            self.assertGreater(r["seq"], last_seq, "seq must be strictly increasing")
            last_seq = r["seq"]

            current_ts = datetime.fromisoformat(r["timestamp"])
            if last_ts is not None:
                self.assertGreaterEqual(current_ts, last_ts, "timestamp must be chronologically non-decreasing")
            last_ts = current_ts

            self.assertIn("state", r)
            self.assertIn("previous_state", r)


    def test_rime_live_catalog_and_preflight_check(self) -> None:
        """
        Organizer Preflight Verification:
        Confirm exact model/voice/language and transport against Rime live catalog and plugin specifications:
        - Model ID: 'coda' (Rime flagship conversational model)
        - Voice/Speaker: 'celeste' (American female voice available on Coda)
        - Language: 'eng' (English in Rime's supported catalog)
        - Sample Rate: 22050 Hz (standard for Rime Coda)
        - Transports:
          * HTTP chunked streaming (default): https://users.rime.ai/v1/rime-tts
          * WebSocket bidirectional streaming: wss://users-ws.rime.ai
        """
        import typing
        from livekit.plugins import rime

        # 1. Verify Model ID against Rime TTSModels enum
        model_args = typing.get_args(rime.models.TTSModels)
        self.assertIn("coda", model_args, "Model 'coda' must be in Rime TTSModels")

        # 2. Verify Language Code against Rime TTSLangs enum
        lang_args = typing.get_args(rime.langs.TTSLangs)
        self.assertIn("eng", lang_args, "Language 'eng' must be in Rime TTSLangs")

        # 3. Verify HTTP transport configuration
        rime_http, name_http, rate_http = create_tts_provider(
            "rime", model="coda", speaker="celeste", lang="eng", use_websocket=False
        )
        self.assertEqual(name_http, "rime")
        self.assertEqual(rate_http, 22050)
        self.assertEqual(rime_http._opts.model, "coda")
        self.assertEqual(rime_http._opts.speaker, "celeste")
        self.assertEqual(rime_http._opts.coda_options.lang, "eng")
        self.assertEqual(rime_http._base_url, "https://users.rime.ai/v1/rime-tts")
        self.assertFalse(rime_http.capabilities.streaming)

        # 4. Verify WebSocket transport configuration
        rime_ws, name_ws, rate_ws = create_tts_provider(
            "rime", model="coda", speaker="celeste", lang="eng", use_websocket=True
        )
        self.assertEqual(name_ws, "rime")
        self.assertEqual(rate_ws, 22050)
        self.assertEqual(rime_ws._opts.model, "coda")
        self.assertEqual(rime_ws._opts.speaker, "celeste")
        self.assertEqual(rime_ws._opts.coda_options.lang, "eng")
        self.assertEqual(rime_ws._base_url, "wss://users-ws.rime.ai")
        self.assertTrue(rime_ws.capabilities.streaming)


if __name__ == "__main__":
    unittest.main()

