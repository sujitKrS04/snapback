import { useEffect, useRef } from "react";
import { motion } from "framer-motion";

interface LiveWaveformProps {
  levels: number[];
  color: string;
  label: string;
  barWidth?: number;
  maxHeight?: number;
  onSnap?: boolean;
}

export function LiveWaveform({
  levels,
  color,
  label,
  barWidth = 4,
  maxHeight = 44,
  onSnap = false,
}: LiveWaveformProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!onSnap) return;
    const el = containerRef.current;
    if (!el) return;
    el.classList.remove("waveform-snap-flash");
    void el.offsetWidth;
    el.classList.add("waveform-snap-flash");
  }, [onSnap]);

  // Compute peak level across the current buffer
  const peakLevel = levels.length > 0 ? Math.max(...levels) : 0;
  const isActive = peakLevel > 0.05;

  return (
    <div
      ref={containerRef}
      className="glass-card flex flex-col gap-2 p-3 rounded-xl transition-colors duration-200"
    >
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <span
            className="w-2 h-2 rounded-full transition-transform duration-200"
            style={{
              backgroundColor: color,
              boxShadow: isActive ? `0 0 8px ${color}` : "none",
              transform: isActive ? "scale(1.2)" : "scale(1)",
            }}
          />
          <span className="font-mono text-[11px] font-semibold tracking-wider uppercase text-[var(--text-secondary)]">
            {label}
          </span>
        </div>
        <div className="flex items-center gap-1 font-mono text-[10px] text-[var(--text-muted)]">
          <span>{Math.round(peakLevel * 100)}%</span>
          {onSnap && (
            <span className="ml-1 px-1 py-0.2 rounded text-[9px] font-bold bg-red-500/20 text-red-500 animate-pulse">
              SNAP
            </span>
          )}
        </div>
      </div>

      <div
        className="flex items-end justify-between gap-[3px] h-[44px] px-1 overflow-hidden rounded-lg bg-[var(--bg-base)]/50 border border-[var(--border-subtle)]"
        style={{ height: maxHeight }}
      >
        {levels.map((v, i) => {
          const heightPx = Math.max(3, Math.round(v * maxHeight));
          const isHigh = v > 0.7;
          return (
            <motion.div
              key={i}
              className="rounded-full transition-all"
              animate={{
                height: `${heightPx}px`,
                opacity: v > 0.05 ? 0.95 : 0.25,
              }}
              transition={{ duration: 0.05, ease: "easeOut" }}
              style={{
                width: barWidth,
                backgroundColor: isHigh && onSnap ? "var(--state-interrupted)" : color,
                boxShadow: isHigh ? `0 0 6px ${color}` : "none",
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

