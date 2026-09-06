import { motion, AnimatePresence } from "framer-motion";
import { useState } from "react";

interface IntroScreenProps {
  onStart: () => void;
}

export function IntroScreen({ onStart }: IntroScreenProps) {
  const [showProblem, setShowProblem] = useState(false);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-[var(--bg-primary)] text-[var(--text-primary)] px-4 py-12">
      <motion.h1
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
        className="font-display text-4xl md:text-5xl font-semibold tracking-tight mb-8"
      >
        Snapback
      </motion.h1>

      <motion.p
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 20 }}
        transition={{ duration: 0.5, delay: 0.2, ease: [0.22, 1, 0.36, 1] }}
        className="text-lg text-[var(--text-secondary)] max-w-xl text-center mb-8 leading-relaxed"
      >
        A live voice-agent demo exposing real-time state, waveforms, and an interrupt
        barge-in mechanism. Judges: open the live link cold — the agent begins listening,
        the user speaks, and a hard voice-problem interrupt can seize control mid-turn.
      </motion.p>

      <div className="flex flex-col sm:flex-row items-center gap-4 mb-8">
        <motion.button
          onClick={onStart}
          whileTap={{ scale: 0.96 }}
          whileHover={{ scale: 1.02 }}
          className="btn-press rounded-full px-8 py-3 text-sm font-medium text-white transition-colors cursor-pointer"
          style={{ backgroundColor: "var(--accent)" }}
        >
          Start Demo
        </motion.button>
        <button
          onClick={() => setShowProblem((prev) => !prev)}
          className="text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-4 transition-colors cursor-pointer"
        >
          {showProblem ? "Hide details" : "How it works"}
        </button>
      </div>

      <AnimatePresence>
        {showProblem && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            className="flex flex-col items-center gap-6 max-w-2xl overflow-hidden"
          >
            <motion.h2
              className="font-display text-2xl font-semibold mb-2"
              style={{ color: "var(--accent)" }}
            >
              How it works
            </motion.h2>
            <ol className="list-decimal list-inside text-sm text-[var(--text-secondary)] max-w-xl space-y-3">
              <li>
                The agent pipeline logs every state transition as structured JSON lines to
                logs/agent.log.
              </li>
              <li>
                Real SSE events at GET /events?tail=300 stream these lines to
                the frontend, with server-side monotonic seq dedup per run.
              </li>
              <li>
                The frontend maps agent states to visual colors: idle (#6B7280), listening
                (#E8A838), speaking (#F59E0B), interrupted (#DC2626).
              </li>
              <li>
                A hard interrupt (cancel_active()) resolves in ~1ms pipeline latency,
                streaming interrupt-detected then tts-cancelled events.
              </li>
            </ol>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}