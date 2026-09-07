import { useEffect, useRef, useState } from "react";
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
import { ThemeToggle } from "./ThemeToggle";

interface CallScreenProps {
  onReturnToIntro?: () => void;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const ROOM_NAME = import.meta.env.VITE_ROOM_NAME ?? "snapback-call";

const QUICK_BARGE_PROMPTS = [
  "Wait, check Friday instead",
  "Actually, cancel that booking",
  "Hold on, what time was that?",
  "Let's switch to 2:00 PM",
];

export function CallScreen({ onReturnToIntro }: CallScreenProps) {
  const stateMachine = useCallState();
  const { syncState } = stateMachine;
  const [isConnecting, setIsConnecting] = useState(false);
  const [interruptFlashKey, setInterruptFlashKey] = useState(0);
  const prevEventCountRef = useRef(0);
  const [recentLatency, setRecentLatency] = useState<number | null>(null);
  const waveformRef = useRef<HTMLDivElement>(null);
  const [activeTab, setActiveTab] = useState<"transcript" | "logs">("transcript");
  const [copiedPrompt, setCopiedPrompt] = useState<string | null>(null);

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

  // Compute dynamic audio level for the Acoustic Resonator
  const activeAudioLevel =
    stateMachine.state === "speaking"
      ? (agentLevels.length ? Math.max(...agentLevels) : 0)
      : stateMachine.state === "listening"
      ? (micLevels.length ? Math.max(...micLevels) : 0)
      : 0;

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
    void el.offsetWidth;
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

  const handleCopyPrompt = (prompt: string) => {
    try {
      navigator.clipboard.writeText(prompt);
      setCopiedPrompt(prompt);
      setTimeout(() => setCopiedPrompt(null), 2500);
    } catch {}
  };

  const streamStatus =
    eventStatus === "connected" ? "live"
    : eventStatus === "reconnecting" ? "reconnecting..."
    : eventStatus === "connecting" ? "connecting..."
    : "disconnected";

  return (
    <div className="flex min-h-screen flex-col items-center justify-start px-4 py-6 sm:px-8 max-w-5xl mx-auto">
      {/* Top Header Bar */}
      <header className="flex w-full items-center justify-between border-b border-[var(--border-subtle)] pb-4 mb-6">
        <div className="flex items-center gap-3">
          {onReturnToIntro && (
            <button
              onClick={onReturnToIntro}
              className="font-mono text-xs px-2.5 py-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors flex items-center gap-1.5 cursor-pointer"
              title="Return to Overview"
            >
              <span>←</span>
              <span className="hidden sm:inline">Overview</span>
            </button>
          )}

          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-[var(--accent)] flex items-center justify-center text-white font-bold text-sm">
              S
            </div>
            <div>
              <h1 className="font-display text-base font-bold tracking-tight text-[var(--text-primary)] leading-tight">
                Snapback Studio
              </h1>
              <div className="font-mono text-[10px] text-[var(--text-muted)]">
                {ROOM_NAME}
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          {/* Status chips */}
          <span className="hidden md:inline-flex items-center gap-1.5 font-mono text-[11px] px-2.5 py-1 rounded-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                streamStatus === "live" ? "bg-emerald-500 animate-pulse" : "bg-zinc-500"
              }`}
            />
            SSE: {streamStatus}
          </span>

          {isConnected && (
            agentTrack ? (
              <span className="hidden sm:inline-flex items-center gap-1.5 font-mono text-[11px] px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-500 font-semibold">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                Rime Agent Online
              </span>
            ) : (
              <span className="hidden sm:inline-flex items-center gap-1.5 font-mono text-[11px] px-2.5 py-1 rounded-full bg-amber-500/10 border border-amber-500/20 text-amber-500 font-semibold">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping" />
                Agent Connecting…
              </span>
            )
          )}

          {/* Record button */}
          <button
            onClick={isRecording ? stopRecording : startRecording}
            className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition-all cursor-pointer ${
              isRecording
                ? "bg-red-500/20 text-red-500 border border-red-500/50 animate-pulse shadow-md"
                : "glass-card text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)]"
            }`}
            title={isRecording ? "Stop recording video" : "Record demo clip (.webm)"}
          >
            <span
              className={`inline-block h-2 w-2 rounded-full ${
                isRecording ? "bg-red-500" : "bg-zinc-400"
              }`}
            />
            <span>{isRecording ? "REC 00:Live" : "Record Clip"}</span>
          </button>

          <ThemeToggle />
        </div>
      </header>

      {/* Main Studio Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 w-full items-start">
        {/* Left Column: Resonator & Live Hardware Stage (col-span-7) */}
        <div className="lg:col-span-6 flex flex-col items-center gap-6">
          {/* Acoustic Resonator */}
          <div className="glass-card w-full flex flex-col items-center justify-center p-8 rounded-3xl border border-[var(--border-subtle)] relative overflow-hidden">
            {/* Background radial gradient */}
            <div className="absolute inset-0 bg-radial from-[var(--accent)]/5 to-transparent pointer-events-none" />

            <StateIndicator
              state={stateMachine.state}
              interruptFlashKey={interruptFlashKey}
              audioLevel={activeAudioLevel}
              size={180}
            />

            {/* Connecting animation */}
            <AnimatePresence>
              {isConnecting && !isConnected && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="mt-4 flex items-center gap-2 text-xs font-mono text-[var(--text-secondary)]"
                >
                  <span className="w-2 h-2 rounded-full bg-[var(--accent)] animate-ping" />
                  Negotiating WebRTC handshake…
                </motion.div>
              )}
              {isConnected && !agentTrack && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="mt-4 flex items-center gap-2 text-xs font-mono text-amber-500"
                >
                  <span className="w-2 h-2 rounded-full bg-amber-500 animate-ping" />
                  Voice Agent is connecting to room…
                </motion.div>
              )}
            </AnimatePresence>

            {/* Recent Latency Pill */}
            <AnimatePresence>
              {recentLatency !== null && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.9, y: 10 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.9, y: -10 }}
                  className="mt-4 inline-flex items-center gap-2 rounded-full bg-red-500/10 border border-red-500/30 px-3.5 py-1.5 shadow-lg"
                >
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                  <span className="font-mono text-[11px] font-bold text-red-500">
                    Barge-In Cancel: {recentLatency.toFixed(1)}ms
                  </span>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          {/* Primary Call Controls */}
          <div className="flex items-center gap-3 w-full justify-center">
            <button
              onClick={handleStartCall}
              disabled={isConnecting}
              className={`btn-tactile px-8 py-3.5 rounded-full text-sm font-semibold transition-all shadow-lg cursor-pointer flex items-center gap-2 ${
                isConnected
                  ? "bg-red-500 hover:bg-red-600 text-white shadow-red-500/20"
                  : ""
              }`}
            >
              {isConnecting ? (
                <>
                  <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  <span>Connecting…</span>
                </>
              ) : isConnected ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
                  <span>End Studio Call</span>
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                    <path d="M20.01 15.38c-1.23 0-2.42-.2-3.53-.56a.977.977 0 00-1.01.24l-1.57 1.97c-2.83-1.35-5.48-3.9-6.89-6.83l1.95-1.66c.27-.28.35-.67.24-1.02-.37-1.11-.56-2.3-.56-3.53 0-.54-.45-.99-.99-.99H4.19C3.65 3 3 3.24 3 3.99 3 13.28 10.73 21 20.01 21c.71 0 .99-.63.99-1.18v-3.45c0-.54-.45-.99-.99-.99z" />
                  </svg>
                  <span>Start Live Session</span>
                </>
              )}
            </button>

            {isConnected && (
              <button
                onClick={toggleMicrophone}
                className="glass-card px-5 py-3.5 rounded-full text-sm font-medium border border-[var(--border-subtle)] text-[var(--text-primary)] hover:bg-[var(--bg-surface-elevated)] transition-colors cursor-pointer flex items-center gap-2"
              >
                <span
                  className={`w-2 h-2 rounded-full ${
                    isMicrophoneEnabled ? "bg-emerald-500" : "bg-zinc-500"
                  }`}
                />
                <span>{isMicrophoneEnabled ? "Mute Mic" : "Unmute Mic"}</span>
              </button>
            )}
          </div>

          {/* Dual Channel Audio Waveforms */}
          {isConnected && (
            <div
              ref={waveformRef}
              className="grid grid-cols-2 gap-3 w-full"
            >
              <LiveWaveform
                levels={micLevels}
                color="var(--state-listening)"
                label="Mic In (You)"
                onSnap={interruptFlashKey > 0}
              />
              <LiveWaveform
                levels={agentLevels}
                color="var(--state-speaking)"
                label="Voice Out (Rime)"
                onSnap={interruptFlashKey > 0}
              />
            </div>
          )}

          {/* Quick-Barge Prompt Chips */}
          <div className="glass-card w-full p-4 rounded-2xl border border-[var(--border-subtle)]">
            <div className="flex items-center justify-between mb-2.5">
              <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-[var(--text-secondary)]">
                Try Interrupting With:
              </span>
              <span className="font-mono text-[10px] text-[var(--text-muted)]">
                {copiedPrompt ? "✓ Copied to clipboard!" : "Click prompt to copy"}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {QUICK_BARGE_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => handleCopyPrompt(prompt)}
                  className="font-mono text-[11px] px-3 py-1.5 rounded-lg bg-[var(--bg-base)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--accent)] transition-colors cursor-pointer text-left"
                >
                  "{prompt}"
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Right Column: Tabbed Transcript & System Telemetry Deck (col-span-5) */}
        <div className="lg:col-span-6 flex flex-col gap-4 w-full">
          {/* Tab Bar */}
          <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-2">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setActiveTab("transcript")}
                className={`font-mono text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors cursor-pointer flex items-center gap-1.5 ${
                  activeTab === "transcript"
                    ? "bg-[var(--accent)] text-white shadow-sm"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                }`}
              >
                <span>Live Dialogue</span>
                <span className="text-[10px] opacity-80 font-mono">({transcripts.length})</span>
              </button>
              <button
                onClick={() => setActiveTab("logs")}
                className={`font-mono text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors cursor-pointer flex items-center gap-1.5 ${
                  activeTab === "logs"
                    ? "bg-[var(--accent)] text-white shadow-sm"
                    : "text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                }`}
              >
                <span>Telemetry SSE</span>
                <span className="text-[10px] opacity-80 font-mono">({events.length})</span>
              </button>
            </div>

            <span className="font-mono text-[10px] text-[var(--text-muted)]">
              {activeTab === "transcript" ? "Deepgram + Rime" : "FastAPI /events"}
            </span>
          </div>

          {/* Active Tab View */}
          <div className="w-full">
            <AnimatePresence mode="wait">
              {activeTab === "transcript" ? (
                <motion.div
                  key="transcript-view"
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.15 }}
                >
                  <TranscriptPanel transcripts={transcripts} />
                </motion.div>
              ) : (
                <motion.div
                  key="logs-view"
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.15 }}
                >
                  <SystemLogPanel events={events} />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>
    </div>
  );
}

