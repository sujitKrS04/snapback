import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  type AgentLogEvent,
  stageBadgeColor,
  BADGE_STYLES,
} from "../lib/agentEvents";

interface SystemLogPanelProps {
  events: AgentLogEvent[];
}

function formatTime(ts?: string): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      fractionalSecondDigits: 3,
    } as Intl.DateTimeFormatOptions);
  } catch {
    return ts;
  }
}

function shortRunId(runId?: string): string {
  if (!runId) return "—";
  const parts = runId.split("-");
  return parts.length > 1 ? parts.slice(-2).join("-") : runId.slice(-8);
}

export function SystemLogPanel({ events }: SystemLogPanelProps) {
  const [open, setOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events, open]);

  return (
    <div className="w-full rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] transition-colors rounded-t-xl"
      >
        <span>System Log ({events.length})</span>
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          className="text-[10px]"
        >
          ▼
        </motion.span>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="overflow-hidden"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="max-h-[320px] overflow-y-auto scrollbar-thin border-t border-[var(--border-color)] bg-[var(--bg-primary)]">
              {events.length === 0 && (
                <p className="px-4 py-6 text-center text-xs text-[var(--text-secondary)]">
                  Waiting for events…
                </p>
              )}
              {events.map((ev, i) => {
                const badge = stageBadgeColor(ev.stage);
                const badgeStyle = BADGE_STYLES[badge] || BADGE_STYLES.neutral;
                return (
                  <motion.div
                    key={`${ev.run_id}-${ev.seq}-${i}`}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.2, delay: i * 0.02 }}
                    className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-[var(--border-color)]/30 px-4 py-1.5 font-mono text-[11px] leading-relaxed last:border-0 hover:bg-[var(--bg-surface)]/50 transition-colors"
                  >
                    <span className="shrink-0 text-[var(--text-secondary)]/60">
                      {formatTime(ev.timestamp)}
                    </span>
                    <span className="shrink-0 tabular-nums text-[var(--text-secondary)]/50">
                      #{ev.seq ?? "—"}
                    </span>
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${badgeStyle}`}
                    >
                      {ev.stage ?? "unknown"}
                    </span>
                    {ev.state && (
                      <span className="text-[var(--text-secondary)]/70">
                        {ev.state}
                      </span>
                    )}
                    {ev.latency_ms !== undefined && (
                      <span className="shrink-0 tabular-nums text-[var(--state-speaking)]">
                        {ev.latency_ms.toFixed(1)}ms
                      </span>
                    )}
                    <span className="ml-auto text-[var(--text-secondary)]/40">
                      {shortRunId(ev.run_id)}
                    </span>
                  </motion.div>
                );
              })}
              <div ref={bottomRef} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
