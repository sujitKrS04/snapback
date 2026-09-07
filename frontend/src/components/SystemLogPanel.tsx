import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { type AgentLogEvent } from "../lib/agentEvents";

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
  return parts.length > 1 ? parts.slice(-2).join("-") : runId.slice(-6);
}

function getBadgeProps(stage?: string) {
  if (!stage) return { bg: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30", label: "event" };
  if (
    stage === "interrupt-detected" ||
    stage === "tts-cancelled" ||
    stage === "tool-cancelled"
  ) {
    return { bg: "bg-red-500/15 text-red-500 border-red-500/30 font-bold", label: stage };
  }
  if (stage === "tts-start" || stage === "tts-speaking") {
    return { bg: "bg-amber-500/15 text-amber-500 border-amber-500/30", label: stage };
  }
  if (stage === "speech-start" || stage === "listening") {
    return { bg: "bg-sky-500/15 text-sky-500 border-sky-500/30", label: stage };
  }
  if (stage.startsWith("tool-") || stage === "stale-result-discarded") {
    return { bg: "bg-purple-500/15 text-purple-400 border-purple-500/30", label: stage };
  }
  return { bg: "bg-[var(--bg-base)] text-[var(--text-secondary)] border-[var(--border-subtle)]", label: stage };
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
    <div className="glass-card w-full rounded-2xl border border-[var(--border-subtle)] overflow-hidden transition-colors duration-200">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-[var(--bg-surface-elevated)] cursor-pointer"
      >
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-[var(--accent)] opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-[var(--accent)]"></span>
          </span>
          <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-[var(--text-secondary)]">
            Telemetry Stream
          </span>
          <span className="font-mono text-[10px] px-2 py-0.5 rounded-full bg-[var(--bg-base)] border border-[var(--border-subtle)] text-[var(--text-muted)]">
            {events.length} msgs
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono text-[var(--text-muted)]">
            {open ? "COLLAPSE" : "EXPAND"}
          </span>
          <motion.span
            animate={{ rotate: open ? 180 : 0 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            className="text-[11px] text-[var(--text-secondary)]"
          >
            ▼
          </motion.span>
        </div>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            className="overflow-hidden border-t border-[var(--border-subtle)] bg-[var(--bg-base)]/80"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="max-h-[280px] overflow-y-auto scrollbar-thin p-2 space-y-1">
              {events.length === 0 ? (
                <p className="py-8 text-center font-mono text-xs text-[var(--text-muted)]">
                  Waiting for SSE telemetry from GET /events…
                </p>
              ) : (
                events.map((ev, i) => {
                  const { bg, label } = getBadgeProps(ev.stage);
                  const isInterrupt = ev.stage === "interrupt-detected";
                  return (
                    <div
                      key={`${ev.run_id}-${ev.seq}-${i}`}
                      className={`flex flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg px-2.5 py-1.5 font-mono text-[11px] transition-colors ${
                        isInterrupt
                          ? "bg-red-500/10 border border-red-500/30"
                          : "hover:bg-[var(--bg-surface)]"
                      }`}
                    >
                      <span className="tabular-nums text-[var(--text-muted)] text-[10px]">
                        {formatTime(ev.timestamp)}
                      </span>
                      <span className="tabular-nums text-[var(--text-muted)] font-semibold">
                        #{ev.seq ?? "—"}
                      </span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] border ${bg}`}
                      >
                        {label}
                      </span>
                      {ev.state && (
                        <span className="text-[var(--text-secondary)]">
                          {ev.state}
                        </span>
                      )}
                      {ev.latency_ms !== undefined && (
                        <span className="ml-auto inline-flex items-center gap-1 font-bold tabular-nums text-emerald-500 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded text-[10px]">
                          ⚡ {ev.latency_ms.toFixed(1)}ms
                        </span>
                      )}
                      <span className="text-[10px] text-[var(--text-muted)] ml-auto">
                        {shortRunId(ev.run_id)}
                      </span>
                    </div>
                  );
                })
              )}
              <div ref={bottomRef} />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

