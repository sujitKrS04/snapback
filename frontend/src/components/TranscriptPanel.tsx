import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import type { TranscriptEntry } from "../hooks/useLiveKit";

interface TranscriptPanelProps {
  transcripts: TranscriptEntry[];
}

export function TranscriptPanel({ transcripts }: TranscriptPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcripts]);

  return (
    <div className="glass-card flex flex-col overflow-hidden rounded-2xl border border-[var(--border-subtle)] transition-colors duration-200">
      {/* Panel Header */}
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4 py-3 bg-[var(--bg-surface)]/60">
        <div className="flex items-center gap-2">
          <svg
            className="w-3.5 h-3.5 text-[var(--accent)]"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
            />
          </svg>
          <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-[var(--text-secondary)]">
            Live Dialogue Stream
          </span>
        </div>
        <span className="font-mono text-[10px] px-2 py-0.5 rounded-full bg-[var(--bg-base)] border border-[var(--border-subtle)] text-[var(--text-muted)]">
          {transcripts.length} {transcripts.length === 1 ? "turn" : "turns"}
        </span>
      </div>

      {/* Transcript Scrollable Body */}
      <div
        className="flex flex-col gap-3 overflow-y-auto scrollbar-thin px-4 py-4"
        style={{ maxHeight: 260 }}
      >
        {transcripts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-center text-xs text-[var(--text-muted)]">
            <svg
              className="w-8 h-8 mb-2 opacity-30 stroke-current"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 100-6 3 3 0 000 6z"
              />
            </svg>
            <span>Start speaking or ask a question to test live interrupt...</span>
          </div>
        ) : (
          transcripts.map((t) => {
            const isAgent = t.speaker === "agent";
            return (
              <motion.div
                key={t.id}
                initial={{ opacity: 0, y: 8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                className={`flex flex-col max-w-[90%] gap-1 ${
                  isAgent ? "self-start items-start" : "self-end items-end"
                }`}
              >
                <div className="flex items-center gap-1.5 px-1 text-[10px] font-mono">
                  <span
                    className="w-1.5 h-1.5 rounded-full"
                    style={{
                      backgroundColor: isAgent
                        ? "var(--state-speaking)"
                        : "var(--state-listening)",
                    }}
                  />
                  <span
                    className="font-semibold uppercase tracking-wider"
                    style={{
                      color: isAgent
                        ? "var(--state-speaking)"
                        : "var(--state-listening)",
                    }}
                  >
                    {isAgent ? "Snapback Agent (Rime)" : "You"}
                  </span>
                </div>

                <div
                  className={`rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
                    isAgent
                      ? "bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] text-[var(--text-primary)] shadow-sm rounded-tl-sm"
                      : "bg-[var(--accent)] text-white font-normal shadow-sm rounded-tr-sm"
                  }`}
                >
                  {t.text}
                </div>
              </motion.div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

