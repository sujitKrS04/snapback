import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { CallState } from "../hooks/useCallState";
import { STATE_COLORS, STATE_LABELS } from "../hooks/useCallState";

interface StateIndicatorProps {
  state: CallState;
  interruptFlashKey: number;
  size?: number;
}

function glowShadow(color: string, alpha = 0.35): string {
  return `0 0 48px ${color}${Math.round(alpha * 255)
    .toString(16)
    .padStart(2, "0")}, 0 0 96px ${color}${Math.round(alpha * 0.5 * 255)
    .toString(16)
    .padStart(2, "0")}`;
}

const INTERRUPT_RED = "#dc2626";

export function StateIndicator({
  state,
  interruptFlashKey,
  size = 192,
}: StateIndicatorProps) {
  const color = STATE_COLORS[state];
  const label = STATE_LABELS[state];
  const isSpeaking = state === "speaking";
  const isIdle = state === "idle";

  const [flashState, setFlashState] = useState({ key: 0, visible: false });
  const [prevFlashKey, setPrevFlashKey] = useState(0);

  if (interruptFlashKey !== prevFlashKey) {
    setPrevFlashKey(interruptFlashKey);
    setFlashState({ key: interruptFlashKey, visible: true });
  }

  useEffect(() => {
    if (!flashState.visible) return;
    const t = setTimeout(
      () =>
        setFlashState((f) => ({ key: f.key, visible: false })),
      420,
    );
    return () => clearTimeout(t);
  }, [flashState.key, flashState.visible]);

  const animateSpeaking = isSpeaking;
  const scaleBase: number | number[] = animateSpeaking ? [1, 1.03, 1] : 1;
  const glowBase = animateSpeaking
    ? [glowShadow(color, 0.3), glowShadow(color, 0.6), glowShadow(color, 0.3)]
    : glowShadow(color, 0.35);

  // Idle: subtle breathing
  const animateIdle = isIdle && !isSpeaking;

  return (
    <div className="flex flex-col items-center gap-4">
      <div className="relative" style={{ width: size, height: size }}>
        {/* Base circle */}
        <motion.div
          className="absolute inset-0 rounded-full"
          animate={{
            backgroundColor: color,
            scale: scaleBase,
            boxShadow: glowBase,
          }}
          transition={
            animateSpeaking
              ? {
                  backgroundColor: { duration: 0.4, ease: "easeOut" },
                  scale: { duration: 1.6, repeat: Infinity, ease: "easeInOut" },
                  boxShadow: { duration: 1.6, repeat: Infinity, ease: "easeInOut" },
                }
              : animateIdle
                ? {
                    backgroundColor: { duration: 0.6, ease: "easeOut" },
                    scale: { duration: 3, repeat: Infinity, ease: "easeInOut" },
                    boxShadow: { duration: 3, repeat: Infinity, ease: "easeInOut" },
                  }
                : { duration: 0.4, ease: "easeOut" }
          }
        />

        {/* Idle breathing ring */}
        <AnimatePresence>
          {animateIdle && (
            <motion.div
              className="absolute inset-[-4px] rounded-full border-2"
              style={{ borderColor: color }}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: [0.3, 0.6, 0.3], scale: [0.95, 1.05, 0.95] }}
              exit={{ opacity: 0 }}
              transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
            />
          )}
        </AnimatePresence>

        {/* Speaking choreography ring */}
        <AnimatePresence>
          {animateSpeaking && (
            <motion.div
              className="absolute inset-[-6px] rounded-full border-2"
              style={{ borderColor: color }}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: [0.2, 0.5, 0.2], scale: [0.9, 1.1, 0.9] }}
              exit={{ opacity: 0 }}
              transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
            />
          )}
        </AnimatePresence>

        {/* Interrupt flash overlay */}
        <AnimatePresence>
          {flashState.visible && (
            <motion.div
              key={`flash-${flashState.key}`}
              className="absolute inset-0 rounded-full"
              initial={{
                scale: 0.82,
                opacity: 0.95,
                boxShadow: `0 0 60px ${INTERRUPT_RED}CC, 0 0 120px ${INTERRUPT_RED}88`,
              }}
              animate={{
                scale: 1.2,
                opacity: 0,
                boxShadow: `0 0 20px ${INTERRUPT_RED}00, 0 0 40px ${INTERRUPT_RED}00`,
              }}
              exit={{ opacity: 0 }}
              transition={{
                duration: 0.38,
                ease: [0.22, 1, 0.36, 1],
              }}
            />
          )}
        </AnimatePresence>
      </div>

      <span
        className="text-sm font-medium tracking-wide uppercase"
        style={{ color }}
      >
        {label}
      </span>
    </div>
  );
}
