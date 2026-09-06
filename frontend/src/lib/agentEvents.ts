export type VisualCallState =
  | "idle"
  | "listening"
  | "speaking"
  | "interrupted"
  | "processing";

export interface AgentLogEvent {
  timestamp?: string;
  seq?: number;
  run_id?: string;
  stage?: string;
  state?: string;
  previous_state?: string;
  active_tts_provider?: string;
  request_id?: string;
  latency_ms?: number;
  [key: string]: unknown;
}

const AGENT_STATE_MAP: Record<string, VisualCallState> = {
  idle: "idle",
  listening: "listening",
  "tool-pending": "processing",
  "tool-running": "processing",
  "tool-completed": "processing",
  "tts-speaking": "speaking",
};

export function mapAgentState(
  state: string | undefined,
): VisualCallState | null {
  if (!state) return null;
  return AGENT_STATE_MAP[state] ?? "idle";
}

export function isInterruptDetected(ev: AgentLogEvent): boolean {
  return ev.stage === "interrupt-detected";
}

export function parseJsonLine(raw: string): AgentLogEvent | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed) as AgentLogEvent;
  } catch {
    return null;
  }
}

export class SeqDeduplicator {
  private maxSeqByRun: Record<string, number> = {};
  private seenFlat: Set<string> = new Set();

  accept(ev: AgentLogEvent): boolean {
    const runId = ev.run_id;
    const seq = ev.seq;
    if (typeof runId === "string" && typeof seq === "number") {
      const last = this.maxSeqByRun[runId] ?? -1;
      if (seq <= last) return false;
      this.maxSeqByRun[runId] = seq;
      return true;
    }
    // Events without run_id/seq: dedupe on full JSON string
    const digest = JSON.stringify(ev);
    if (this.seenFlat.has(digest)) return false;
    this.seenFlat.add(digest);
    return true;
  }
}

export function stageBadgeColor(stage?: string): string {
  if (!stage) return "neutral";
  if (
    stage === "interrupt-detected" ||
    stage === "tts-cancelled" ||
    stage === "tool-cancelled"
  ) {
    return "red";
  }
  if (stage === "tts-start") return "speaking";
  if (stage === "speech-start") return "listening";
  if (stage.startsWith("tool-") || stage === "stale-result-discarded") {
    return "neutral";
  }
  if (stage === "tts-end" || stage === "speech-end") return "idle";
  return "neutral";
}

export const BADGE_STYLES: Record<string, string> = {
  red: "bg-[#E24B4A]/20 text-[#E24B4A] ring-1 ring-[#E24B4A]/30",
  speaking: "bg-[#EF9F27]/15 text-[#EF9F27] ring-1 ring-[#EF9F27]/30",
  listening: "bg-[#378ADD]/15 text-[#378ADD] ring-1 ring-[#378ADD]/30",
  neutral:
    "bg-[var(--bg-surface)] text-[var(--text-secondary)] ring-1 ring-[var(--border-color)]",
  idle: "bg-[var(--bg-surface)] text-[var(--text-secondary)] ring-1 ring-[var(--border-color)]",
};
