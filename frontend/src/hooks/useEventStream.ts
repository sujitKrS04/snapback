import { useEffect, useRef, useState } from "react";
import type { AgentLogEvent } from "../lib/agentEvents";
import { SeqDeduplicator } from "../lib/agentEvents";

export type EventStatus =
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

const MAX_EVENTS = 500;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 15000;

export function useEventStream(url: string) {
  const [status, setStatus] = useState<EventStatus>("disconnected");
  const [events, setEvents] = useState<AgentLogEvent[]>([]);
  const dedupRef = useRef(new SeqDeduplicator());

  useEffect(() => {
    let aborted = false;
    let controller: AbortController | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    // Reset the deduplicator for a fresh stream
    dedupRef.current = new SeqDeduplicator();

    const stop = () => {
      aborted = true;
      controller?.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };

    const openStream = (isReconnect: boolean) => {
      if (aborted) return;
      controller?.abort();
      controller = new AbortController();
      setStatus(isReconnect ? "reconnecting" : "connecting");

      fetch(url, { signal: controller.signal })
        .then(async (resp) => {
          if (!resp.ok || !resp.body) {
            throw new Error(`SSE ${resp.status}`);
          }
          if (aborted) return;
          setStatus("connected");

          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          while (!aborted) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            // Keep the last (potentially incomplete) chunk in the buffer
            buffer = lines.pop() ?? "";

            for (const line of lines) {
              const trimmed = line.trim();
              if (!trimmed || trimmed.startsWith(":")) continue;
              const data = trimmed.startsWith("data: ")
                ? trimmed.slice(6)
                : trimmed;
              if (!data) continue;

              try {
                const ev = JSON.parse(data) as AgentLogEvent;
                if (dedupRef.current.accept(ev)) {
                  setEvents((prev) => {
                    const next = [...prev, ev];
                    return next.length > MAX_EVENTS
                      ? next.slice(-MAX_EVENTS)
                      : next;
                  });
                }
              } catch {
                // Skip malformed lines
              }
            }
          }
        })
        .catch((err: unknown) => {
          if (!aborted) console.error("SSE stream error:", err);
        })
        .finally(() => {
          if (aborted) return;
          setStatus("reconnecting");
          const backoff = Math.min(
            RECONNECT_MAX_MS,
            RECONNECT_BASE_MS * (1 + Math.random() * 0.3),
          );
          reconnectTimer = setTimeout(() => openStream(true), backoff);
        });
    };

    openStream(false);

    return stop;
  }, [url]);

  return { events, status };
}