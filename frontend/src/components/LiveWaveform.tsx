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
  barWidth = 3,
  maxHeight = 48,
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

  return (
    <div ref={containerRef} className="flex flex-col gap-1.5 overflow-hidden">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold tracking-wide uppercase text-[var(--text-secondary)]">
          {label}
        </span>
        {onSnap && (
          <span className="w-2 h-2 rounded-full bg-[var(--state-interrupted)] animate-pulse" />
        )}
      </div>
      <motion.div
        className="flex items-end gap-[2px] rounded-lg overflow-hidden"
        style={{ height: maxHeight }}
        animate={onSnap ? { backgroundColor: "rgba(220,38,38,0.08)" } : {}}
        transition={{ duration: 0.5 }}
      >
        {levels.map((v, i) => (
          <motion.div
            key={i}
            className="rounded-sm"
            animate={{
              height: `${Math.max(4, v * maxHeight)}px`,
              opacity: 0.35 + v * 0.65,
            }}
            transition={{ duration: 0.06, ease: "easeOut" }}
            style={{
              width: barWidth,
              backgroundColor: color,
            }}
          />
        ))}
      </motion.div>
    </div>
  );
}
