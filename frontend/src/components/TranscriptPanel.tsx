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
    <div className="flex flex-col overflow-hidden rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
      <div className="border-b border-[var(--border-color)] px-4 py-2.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
        Transcript
      </div>
      <div className="flex flex-col gap-2 overflow-y-auto scrollbar-thin px-4 py-3" style={{ maxHeight: 280 }}>
        {transcripts.length === 0 && (
          <p className="py-6 text-center text-sm text-[var(--text-secondary)]">
            Transcript will appear here…
          </p>
        )}
        {transcripts.map((t) => (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="flex flex-col gap-0.5"
          >
            <span
              className="text-xs font-semibold"
              style={{
                color:
                  t.speaker === "agent"
                    ? "var(--state-speaking)"
                    : "var(--state-listening)",
              }}
            >
              {t.speaker === "agent" ? "Agent" : "You"}
            </span>
            <span className="text-sm leading-relaxed text-[var(--text-primary)]">
              {t.text}
            </span>
          </motion.div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
