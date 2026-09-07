import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { CallState } from "../hooks/useCallState";

interface StateIndicatorProps {
  state: CallState;
  interruptFlashKey: number;
  size?: number;
  audioLevel?: number; // normalized audio amplitude 0..1
}

interface StateConfig {
  label: string;
  sublabel: string;
  colorVar: string;
  glowVar: string;
  icon: (color: string) => React.ReactNode;
}

const STATE_CONFIG: Record<CallState, StateConfig> = {
  idle: {
    label: "Ready & Listening",
    sublabel: "Awaiting speech or wake query",
    colorVar: "var(--state-idle)",
    glowVar: "var(--state-idle-glow)",
    icon: (color) => (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke={color} strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v7.5a3 3 0 0 1-3 3Z" />
      </svg>
    ),
  },
  listening: {
    label: "User Speaking",
    sublabel: "Deepgram Streaming Nova-3 ASR",
    colorVar: "var(--state-listening)",
    glowVar: "var(--state-listening-glow)",
    icon: (color) => (
      <svg className="w-5 h-5 animate-pulse" fill="none" viewBox="0 0 24 24" stroke={color} strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v7.5a3 3 0 0 1-3 3Z" />
      </svg>
    ),
  },
  speaking: {
    label: "Rime Voice Synthesizing",
    sublabel: "Official Coda / Celeste 22.05 kHz",
    colorVar: "var(--state-speaking)",
    glowVar: "var(--state-speaking-glow)",
    icon: (color) => (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke={color} strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
      </svg>
    ),
  },
  interrupted: {
    label: "Barge-in Interrupted",
    sublabel: "Active turn aborted · Audio queue flushed",
    colorVar: "var(--state-interrupted)",
    glowVar: "var(--state-interrupted-glow)",
    icon: (color) => (
      <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke={color} strokeWidth="2.5">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
      </svg>
    ),
  },
  processing: {
    label: "Fencing Intent & Tools",
    sublabel: "FastAPI Availability Verification",
    colorVar: "var(--state-processing)",
    glowVar: "var(--state-processing-glow)",
    icon: (color) => (
      <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24" stroke={color} strokeWidth="2">
        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
      </svg>
    ),
  },
};

export function StateIndicator({
  state,
  interruptFlashKey,
  size = 200,
  audioLevel = 0,
}: StateIndicatorProps) {
  const [isFlashing, setIsFlashing] = useState(false);
  const [prevFlashKey, setPrevFlashKey] = useState(0);

  if (interruptFlashKey !== prevFlashKey) {
    setPrevFlashKey(interruptFlashKey);
    setIsFlashing(true);
  }

  useEffect(() => {
    if (!isFlashing) return;
    const timer = setTimeout(() => setIsFlashing(false), 450);
    return () => clearTimeout(timer);
  }, [isFlashing, interruptFlashKey]);

  const activeState = isFlashing ? "interrupted" : state;
  const config = STATE_CONFIG[activeState];
  const color = config.colorVar;

  // Reactivity scaling from audioLevel
  const dynamicScale = 1 + Math.min(0.25, audioLevel * 0.4);
  const isSpeaking = activeState === "speaking";
  const isListening = activeState === "listening";

  return (
    <div className="flex flex-col items-center gap-5">
      {/* Resonator Core Stage */}
      <div
        className="relative flex items-center justify-center"
        style={{ width: size, height: size }}
      >
        {/* Instantaneous Snap Shockwave on Interrupt */}
        <AnimatePresence>
          {isFlashing && (
            <motion.div
              key={`shockwave-${interruptFlashKey}`}
              className="absolute inset-0 rounded-full border-2 border-red-500 snap-shockwave pointer-events-none"
              initial={{ scale: 0.8, opacity: 1 }}
              animate={{ scale: 1.8, opacity: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            />
          )}
        </AnimatePresence>

        {/* Outer Harmonic Ring 3 */}
        <motion.div
          className="absolute inset-[-20px] rounded-full border border-dashed opacity-25 pointer-events-none"
          style={{ borderColor: color }}
          animate={{
            scale: isSpeaking || isListening ? [1, dynamicScale * 1.08, 1] : [1, 1.03, 1],
            rotate: isSpeaking ? 360 : 0,
          }}
          transition={{
            scale: { duration: 2.2, repeat: Infinity, ease: "easeInOut" },
            rotate: { duration: 18, repeat: Infinity, ease: "linear" },
          }}
        />

        {/* Mid Soundwave Ring 2 */}
        <motion.div
          className="absolute inset-[-8px] rounded-full border pointer-events-none"
          style={{
            borderColor: color,
            opacity: isSpeaking || isListening ? 0.45 : 0.2,
          }}
          animate={{
            scale: isSpeaking || isListening ? [1, dynamicScale * 1.04, 1] : [1, 1.02, 1],
          }}
          transition={{
            duration: isSpeaking ? 0.8 : 2.5,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />

        {/* Inner Radial Glow Ambient Bed */}
        <motion.div
          className="absolute inset-0 rounded-full filter blur-xl pointer-events-none"
          style={{
            backgroundColor: color,
            opacity: isFlashing ? 0.7 : isSpeaking ? 0.45 : isListening ? 0.35 : 0.18,
          }}
          animate={{
            scale: [1, dynamicScale * 1.05, 1],
          }}
          transition={{
            duration: isFlashing ? 0.2 : 1.8,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />

        {/* Central Physical Sphere */}
        <motion.div
          className="relative flex items-center justify-center rounded-full shadow-2xl overflow-hidden cursor-default"
          style={{
            width: size * 0.76,
            height: size * 0.76,
            background: `radial-gradient(circle at 35% 30%, ${color} 0%, rgba(15, 23, 42, 0.95) 75%, #020617 100%)`,
            border: `1.5px solid color-mix(in srgb, ${color} 45%, rgba(255, 255, 255, 0.25))`,
            boxShadow: `0 12px 36px -6px ${config.glowVar}, inset 0 2px 10px rgba(255, 255, 255, 0.35)`,
          }}
          animate={{
            scale: isFlashing ? [1, 1.15, 0.96, 1] : [1, dynamicScale, 1],
          }}
          transition={{
            scale: isFlashing
              ? { duration: 0.35, ease: "easeOut" }
              : { duration: 1.4, repeat: Infinity, ease: "easeInOut" },
          }}
        >
          {/* Internal Particle Specular Highlight */}
          <div className="absolute top-2 left-3 w-8 h-4 rounded-full bg-white/25 filter blur-[2px] pointer-events-none" />

          {/* Central State Icon */}
          <div className="relative z-10 flex flex-col items-center justify-center text-white drop-shadow-md">
            {config.icon("#ffffff")}
          </div>
        </motion.div>
      </div>

      {/* Floating State Telemetry HUD */}
      <motion.div
        className="flex flex-col items-center gap-1 text-center"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <div className="inline-flex items-center gap-2 rounded-full border border-[var(--border-strong)] bg-[var(--bg-surface-elevated)] px-3.5 py-1 shadow-sm">
          <span
            className="inline-block w-2 h-2 rounded-full"
            style={{
              backgroundColor: color,
              boxShadow: `0 0 8px ${color}`,
            }}
          />
          <span className="font-display text-xs font-semibold tracking-wide text-[var(--text-primary)]">
            {config.label}
          </span>
        </div>
        <span className="text-[11px] font-medium text-[var(--text-tertiary)]">
          {config.sublabel}
        </span>
      </motion.div>
    </div>
  );
}
