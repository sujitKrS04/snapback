import { motion, AnimatePresence } from "framer-motion";
import { useState } from "react";
import { ThemeToggle } from "./ThemeToggle";

interface IntroScreenProps {
  onStart: () => void;
}

export function IntroScreen({ onStart }: IntroScreenProps) {
  const [showArchitecture, setShowArchitecture] = useState(false);

  return (
    <div className="min-h-screen flex flex-col justify-between px-4 py-6 sm:px-8 sm:py-8 max-w-5xl mx-auto">
      {/* Top Bar */}
      <header className="flex items-center justify-between w-full border-b border-[var(--border-subtle)] pb-4">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-[var(--accent)] flex items-center justify-center text-white font-bold shadow-md">
            <svg
              className="w-5 h-5 fill-current"
              viewBox="0 0 24 24"
            >
              <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" />
            </svg>
          </div>
          <div>
            <div className="font-display text-lg font-bold tracking-tight text-[var(--text-primary)]">
              SNAPBACK
            </div>
            <div className="font-mono text-[10px] uppercase tracking-widest text-[var(--text-muted)]">
              Acoustic Barge-In Engine
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden sm:inline-flex items-center gap-1.5 font-mono text-[11px] px-2.5 py-1 rounded-full bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            v2.4 LiveKit + Rime
          </span>
          <ThemeToggle />
        </div>
      </header>

      {/* Hero Section */}
      <main className="flex flex-col items-center justify-center my-auto py-12 text-center">
        {/* Telemetry pill */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="telemetry-chip mb-6 inline-flex items-center gap-2"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)]" />
          <span>ZERO-STALE TRANSCRIPT GUARANTEE</span>
        </motion.div>

        {/* Main Headline */}
        <motion.h1
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
          className="font-display text-4xl sm:text-5xl md:text-6xl font-extrabold tracking-tight text-[var(--text-primary)] max-w-3xl leading-[1.12]"
        >
          Instant Interruption for Real-Time Voice Agents.
        </motion.h1>

        {/* Subtitle */}
        <motion.p
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.2, ease: [0.22, 1, 0.36, 1] }}
          className="mt-6 text-base sm:text-lg text-[var(--text-secondary)] max-w-2xl leading-relaxed font-normal"
        >
          Stop waiting for speech synthesis buffers to drain. Snapback combines sub-50ms
          hard pipeline cancellation with monotonic sequence deduplication to seize control
          mid-turn with zero audio or transcript bleed.
        </motion.p>

        {/* Key Metrics Grid */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.25, ease: [0.22, 1, 0.36, 1] }}
          className="grid grid-cols-2 sm:grid-cols-4 gap-3 my-8 w-full max-w-2xl text-left"
        >
          <div className="glass-card p-3 rounded-xl">
            <div className="font-mono text-[10px] text-[var(--text-muted)] uppercase">Cancel Latency</div>
            <div className="text-xl font-bold font-mono text-[var(--accent)]">10.85 ms</div>
            <div className="text-[10px] text-[var(--text-secondary)]">WebRTC physical median</div>
          </div>
          <div className="glass-card p-3 rounded-xl">
            <div className="font-mono text-[10px] text-[var(--text-muted)] uppercase">Transcript Bleed</div>
            <div className="text-xl font-bold font-mono text-emerald-500">0.00%</div>
            <div className="text-[10px] text-[var(--text-secondary)]">Zero stale leaks</div>
          </div>
          <div className="glass-card p-3 rounded-xl">
            <div className="font-mono text-[10px] text-[var(--text-muted)] uppercase">Voice Engine</div>
            <div className="text-xl font-bold font-mono text-[var(--text-primary)]">Rime Coda</div>
            <div className="text-[10px] text-[var(--text-secondary)]">Ultra-low TTFB TTS</div>
          </div>
          <div className="glass-card p-3 rounded-xl">
            <div className="font-mono text-[10px] text-[var(--text-muted)] uppercase">State Sync</div>
            <div className="text-xl font-bold font-mono text-sky-500">Monotonic</div>
            <div className="text-[10px] text-[var(--text-secondary)]">Server-sent events</div>
          </div>
        </motion.div>

        {/* CTA Buttons */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.3, ease: [0.22, 1, 0.36, 1] }}
          className="flex flex-col sm:flex-row items-center gap-4"
        >
          <button
            onClick={onStart}
            className="btn-tactile px-8 py-3.5 rounded-full text-base font-semibold shadow-lg hover:shadow-[var(--accent)]/20 cursor-pointer flex items-center gap-2 group"
          >
            <span>Launch Live Audio Studio</span>
            <svg
              className="w-4 h-4 transition-transform group-hover:translate-x-1"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
            </svg>
          </button>

          <button
            onClick={() => setShowArchitecture((v) => !v)}
            className="font-mono text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] px-4 py-2 rounded-lg hover:bg-[var(--bg-surface)] transition-colors cursor-pointer"
          >
            {showArchitecture ? "Hide Architectural Specs ↑" : "Inspect Architectural Specs ↓"}
          </button>
        </motion.div>

        {/* Architecture Spec Drawer */}
        <AnimatePresence>
          {showArchitecture && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
              className="w-full max-w-3xl mt-8 overflow-hidden text-left"
            >
              <div className="glass-card p-6 rounded-2xl border border-[var(--border-subtle)] space-y-4">
                <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-3">
                  <h3 className="font-mono text-xs font-bold uppercase tracking-wider text-[var(--accent)]">
                    Barge-In Architectural Flow
                  </h3>
                  <span className="font-mono text-[10px] text-[var(--text-muted)]">
                    LiveKit ↔ Deepgram ↔ Rime Pipeline
                  </span>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 font-mono text-xs text-[var(--text-secondary)]">
                  <div className="p-3 rounded-xl bg-[var(--bg-base)] border border-[var(--border-subtle)]">
                    <div className="font-bold text-[var(--state-listening)] mb-1">01. INGEST & VAD</div>
                    <p className="text-[11px] leading-relaxed text-[var(--text-muted)]">
                      User speech is captured via WebRTC into Silero/Deepgram VAD. On speech detected, a cancel signal triggers synchronously.
                    </p>
                  </div>
                  <div className="p-3 rounded-xl bg-[var(--bg-base)] border border-[var(--border-subtle)]">
                    <div className="font-bold text-red-500 mb-1">02. HARD CANCEL GATE</div>
                    <p className="text-[11px] leading-relaxed text-[var(--text-muted)]">
                      Active Rime TTS audio task is instantly aborted in ~10.85ms. Monotonic generation counter invalidates pending speech chunks.
                    </p>
                  </div>
                  <div className="p-3 rounded-xl bg-[var(--bg-base)] border border-[var(--border-subtle)]">
                    <div className="font-bold text-emerald-500 mb-1">03. ISOLATED RESUME</div>
                    <p className="text-[11px] leading-relaxed text-[var(--text-muted)]">
                      Interim transcripts are cleared. Fresh LLM reasoning begins immediately on the interrupting prompt with zero bleed.
                    </p>
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Footer */}
      <footer className="w-full flex flex-col sm:flex-row items-center justify-between border-t border-[var(--border-subtle)] pt-4 text-xs font-mono text-[var(--text-muted)] gap-2">
        <div>Snapback Benchmark Suite · Real-Time Audio Infrastructure</div>
        <div>Rime Labs Voice AI Hackathon</div>
      </footer>
    </div>
  );
}