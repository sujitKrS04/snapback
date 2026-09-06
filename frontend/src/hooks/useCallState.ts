import { useReducer, useCallback } from "react";
import type { VisualCallState } from "../lib/agentEvents";

export type CallState = VisualCallState;

export const STATE_COLORS: Record<CallState, string> = {
  idle: "var(--state-idle)",
  listening: "var(--state-listening)",
  speaking: "var(--state-speaking)",
  interrupted: "var(--state-interrupted)",
  processing: "var(--state-processing)",
};

export const STATE_LABELS: Record<CallState, string> = {
  idle: "Idle",
  listening: "Listening",
  speaking: "Speaking",
  interrupted: "Interrupted",
  processing: "Processing",
};

export type CallAction =
  | { type: "CONNECT" }
  | { type: "DISCONNECT" }
  | { type: "SYNC_STATE"; state: CallState }
  | { type: "RESET" };

function callReducer(state: CallState, action: CallAction): CallState {
  switch (action.type) {
    case "CONNECT":
    case "DISCONNECT":
    case "RESET":
      return "idle";
    case "SYNC_STATE":
      return action.state;
    default:
      return state;
  }
}

export function useCallState() {
  const [state, dispatch] = useReducer(callReducer, "idle" as CallState);

  const connect = useCallback(() => dispatch({ type: "CONNECT" }), []);
  const disconnect = useCallback(() => dispatch({ type: "DISCONNECT" }), []);
  const reset = useCallback(() => dispatch({ type: "RESET" }), []);
  const syncState = useCallback(
    (state: CallState) => dispatch({ type: "SYNC_STATE", state }),
    [],
  );

  return { state, connect, disconnect, reset, syncState };
}
