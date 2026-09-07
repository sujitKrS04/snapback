import { useEffect, useRef, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ConnectionState } from "livekit-client";
import { useCallState } from "../hooks/useCallState";
import { useLiveKit } from "../hooks/useLiveKit";
import { useEventStream } from "../hooks/useEventStream";
import { useWaveform } from "../hooks/useWaveform";
import { mapAgentState, isInterruptDetected } from "../lib/agentEvents";
import { StateIndicator } from "./StateIndicator";
import { TranscriptPanel } from "./TranscriptPanel";
import { LiveWaveform } from "./LiveWaveform";
import { SystemLogPanel } from "./SystemLogPanel";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const ROOM_NAME = import.meta.env.VITE_ROOM_NAME ?? "snapback-call";

export function CallScreen() {
  const stateMachine = useCallState();
  const { syncState } = stateMachine;
  const [isConnecting, setIsConnecting] = useState(false);
  const [interruptFlashKey, setInterruptFlashKey] = useState(0);
  const [isDark, setIsDark] = useState(() =>
    document.documentElement.dataset.theme !== "light",
  );
  const prevEventCountRef = useRef(0);
  const [recentLatency, setRecentLatency] = useState<number | null>(null);
  const waveformRef = useRef<HTMLDivElement>(null);

  const { events, status: eventStatus } = useEventStream(
    `${API_BASE}/events?tail=300`,
  );

  const {
    connectionState,
    isMicrophoneEnabled,
    connect,
    disconnect,
    toggleMicrophone,
    transcripts,
    micTrack,
    agentTrack,
  } = useLiveKit();

  const micLevels = useWaveform(micTrack);
  const agentLevels = useWaveform(agentTrack);

  const isConnected = connectionState === ConnectionState.Connected;

  // Drive state machine from the real event stream
  useEffect(() => {
    if (events.length === prevEventCountRef.current) return;
    const newEvents = events.slice(prevEventCountRef.current);
    prevEventCountRef.current = events.length;

    for (const ev of newEvents) {
      const mapped = mapAgentState(ev.state);
      if (mapped) syncState(mapped);
      if (isInterruptDetected(ev)) {
        setInterruptFlashKey((k) => k + 1);
        if (ev.latency_ms !== undefined) {
          setRecentLatency(ev.latency_ms);
        }
      }
    }
  }, [events, syncState]);

  // Clear latency readout after a few seconds
  useEffect(() => {
    if (recentLatency === null) return;
    const t = setTimeout(() => setRecentLatency(null), 5000);
    return () => clearTimeout(t);
  }, [recentLatency]);

  // Waveform snap on interrupt
  useEffect(() => {
    if (interruptFlashKey === 0) return;
    const el = waveformRef.current;
    if (!el) return;
    el.classList.remove("waveform-snap-flash");
    void el.offsetWidth; // force reflow
    el.classList.add("waveform-snap-flash");
  }, [interruptFlashKey]);

  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);

  const startRecording = async () => {
    try {
      const displayStream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30 },
        audio: true,
      });

      const audioCtx = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
      const dest = audioCtx.createMediaStreamDestination();

      if (displayStream.getAudioTracks().length > 0) {
        const displayAudioSource = audioCtx.createMediaStreamSource(
          new MediaStream([displayStream.getAudioTracks()[0]])
        );
        displayAudioSource.connect(dest);
      }

      if (micTrack) {
        const micSource = audioCtx.createMediaStreamSource(new MediaStream([micTrack]));
        micSource.connect(dest);
      }

      if (agentTrack) {
        const agentSource = audioCtx.createMediaStreamSource(new MediaStream([agentTrack]));
        agentSource.connect(dest);
      }

      const tracks: MediaStreamTrack[] = [...displayStream.getVideoTracks()];
      if (dest.stream.getAudioTracks().length > 0) {
        tracks.push(dest.stream.getAudioTracks()[0]);
      } else if (displayStream.getAudioTracks().length > 0) {
        tracks.push(displayStream.getAudioTracks()[0]);
      }

      const combinedStream = new MediaStream(tracks);
      const mimeType = MediaRecorder.isTypeSupported("video/webm;codecs=vp9,opus")
        ? "video/webm;codecs=vp9,opus"
        : MediaRecorder.isTypeSupported("video/webm;codecs=vp8,opus")
        ? "video/webm;codecs=vp8,opus"
        : "video/webm";

      const recorder = new MediaRecorder(combinedStream, { mimeType });
      recordedChunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) recordedChunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        const blob = new Blob(recordedChunksRef.current, { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `snapback_live_interrupt_demo_${Date.now()}.webm`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);

        try {
          await fetch(`${API_BASE}/save-recording`, {
            method: "POST",
            body: blob,
          });
        } catch (e) {
          console.error("Could not upload recording to backend:", e);
        }

        displayStream.getTracks().forEach((t) => t.stop());
        setIsRecording(false);
      };

      displayStream.getVideoTracks()[0].onended = () => {
        if (recorder.state !== "inactive") recorder.stop();
      };

      recorder.start(500);
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch (err) {
      console.error("Failed to start screen recording:", err);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
  };

  const handleToggleTheme = useCallback(() => {
    setIsDark((d) => {
      const next = !d;
      document.documentElement.dataset.theme = next ? "dark" : "light";
      try { localStorage.setItem("snapback-theme", next ? "dark" : "light"); } catch {}
      return next;
    });
  }, []);

  async function handleStartCall() {
    if (isConnected) {
      await disconnect();
      stateMachine.disconnect();
      return;
    }
    setIsConnecting(true);
    try {
      const res = await fetch(`${API_BASE}/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room: ROOM_NAME, identity: `user-${Date.now()}` }),
      });
      if (!res.ok) throw new Error(`Token request failed: ${res.status}`);
      const { token, url } = await res.json();
      await connect(token, url);
      stateMachine.connect();
    } catch (err) {
      console.error("Failed to start call:", err);
    } finally {
      setIsConnecting(false);
    }
  }

  const streamStatus =
    eventStatus === "connected" ? "live"
    : eventStatus === "reconnecting" ? "reconnecting..."
    : eventStatus === "connecting" ? "connecting..."
    : "disconnected";

  return (
    <div className="flex min-h-screen flex-col items-center justify-start px-4 responsive-py responsive-px sm:px-6">
      <div className="flex w-full max-w-lg flex-col items-center gap-6 responsive-gap pt-4 sm:gap-8">
        {/* Header */}
        <motion.header
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
          className="flex w-full items-center justify-between"
        >
          <h1 className="font-display text-xl font-semibold tracking-tight text-[var(--text-primary)] sm:text-2xl">
            Snapback
          </h1>
          <div className="flex items-center gap-3">
            <button
              onClick={isRecording ? stopRecording : startRecording}
              className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold transition-all ${
                isRecording
                  ? "bg-red-500/20 text-red-400 border border-red-500/50 animate-pulse"
                  : "bg-[var(--surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)]"
              }`}
              title={isRecording ? "Stop recording video" : "Record demo clip (.webm) with mic and Rime audio"}
            >
              <span className={`inline-block h-2 w-2 rounded-full ${isRecording ? "bg-red-500" : "bg-zinc-400"}`} />
              {isRecording ? "Recording..." : "Record Clip (.webm)"}
            </button>
            {isConnected && (
              <span className="provider-badge">Active speech provider: Rime</span>
            )}
            <button
              onClick={handleToggleTheme}
              className="theme-toggle"
              aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
              title={isDark ? "Light mode" : "Dark mode"}
            />
          </div>
        </motion.header>

        {/* Connecting choreography (shown instead of spinner) */}
        <AnimatePresence mode="wait">
          {isConnecting && !isConnected && (
            <motion.div
              key="choreo"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
              className="flex flex-col items-center gap-4"
            >
              <motion.div
                className="relative"
                style={{ width: 80, height: 80 }}
              >
                <motion.div
                  className="absolute inset-0 rounded-full border-2 border-[var(--accent)]"
                  style={{ borderTopColor: "transparent", borderRightColor: "transparent" }}
                  animate={{ rotate: 360 }}
                  transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }}
                />
                <motion.div
                  className="absolute inset-2 rounded-full border-2 border-[var(--state-listening)]"
                  style={{ borderBottomColor: "transparent", borderLeftColor: "transparent" }}
                  animate={{ rotate: -360 }}
                  transition={{ duration: 0.8, repeat: Infinity, ease: "linear" }}
                />
              </motion.div>
              <motion.span
                className="text-sm font-medium text-[var(--text-secondary)] choreo-pulse"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
              >
                Establishing connection…
              </motion.span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* State indicator */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
        >
          <StateIndicator
            state={stateMachine.state}
            interruptFlashKey={interruptFlashKey}
            size={160}
          />
        </motion.div>

        {/* Connection + stream status */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4, delay: 0.2 }}
          className="flex flex-col items-center gap-1 text-xs text-[var(--text-secondary)]"
        >
          <p>
            {isConnected ? "Connected" : "Disconnected"}
            {isConnected && ` \u00b7 ${ROOM_NAME}`}
          </p>
          <p>Event stream: {streamStatus}</p>
        </motion.div>

        {/* Waveforms */}
        {isConnected && (
          <motion.div
            ref={waveformRef}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: 0.3, ease: [0.22, 1, 0.36, 1] }}
            className="grid w-full grid-cols-2 gap-3 responsive-gap"
          >
            <LiveWaveform
              levels={micLevels}
              color="var(--state-listening)"
              label="You"
              onSnap={interruptFlashKey > 0}
            />
            <LiveWaveform
              levels={agentLevels}
              color="var(--state-speaking)"
              label="Agent"
              onSnap={interruptFlashKey > 0}
            />
          </motion.div>
        )}

        {/* Live latency readout */}
        <AnimatePresence>
          {recentLatency !== null && (
            <motion.div
              initial={{ opacity: 0, y: 8, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -8, scale: 0.95 }}
              transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              className="flex items-center gap-2 rounded-full bg-[var(--accent-soft)] px-4 py-2"
            >
              <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                Interrupt latency
              </span>
              <span className="latency-readout text-lg font-bold tabular-nums text-[var(--accent)]">
                {recentLatency.toFixed(1)}ms
              </span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Controls */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.35 }}
          className="flex gap-3"
        >
          <motion.button
            onClick={handleStartCall}
            disabled={isConnecting}
            whileTap={{ scale: 0.96 }}
            whileHover={{ scale: 1.02 }}
            className="btn-press rounded-full px-8 py-3.5 text-sm font-semibold text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            style={{
              backgroundColor: isConnected ? "var(--state-interrupted)" : "var(--accent)",
            }}
          >
            {isConnecting ? "Connecting…" : isConnected ? "End Call" : "Start Call"}
          </motion.button>

          {isConnected && (
            <motion.button
              onClick={toggleMicrophone}
              whileTap={{ scale: 0.96 }}
              whileHover={{ scale: 1.02 }}
              className="btn-press rounded-full border border-[var(--border-color)] px-5 py-3.5 text-sm font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--bg-surface-hover)]"
            >
              {isMicrophoneEnabled ? "Mute" : "Unmute"}
            </motion.button>
          )}
        </motion.div>

        {/* Transcript — slide-in panel */}
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          transition={{ duration: 0.4, delay: 0.4, ease: [0.22, 1, 0.36, 1] }}
          className="w-full overflow-hidden"
        >
          <TranscriptPanel transcripts={transcripts} />
        </motion.div>

        {/* System log — slide-in panel */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.45, ease: [0.22, 1, 0.36, 1] }}
          className="w-full"
        >
          <SystemLogPanel events={events} />
        </motion.div>
      </div>
    </div>
  );
}
