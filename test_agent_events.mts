
/**
 * Verify the frontend pure logic (agentEvents.ts) against real pipeline events.
 * Run: npx tsx test_agent_events.mts
 */
import { parseJsonLine, mapAgentState, isInterruptDetected, stageBadgeColor, SeqDeduplicator } from "./frontend/src/lib/agentEvents";
import { existsSync, readFileSync } from "fs";
import { resolve } from "path";

const logPath = resolve(process.cwd(), "logs/agent.log");
const raw = existsSync(logPath)
  ? readFileSync(logPath, "utf-8")
  : `{"timestamp":"2026-09-07T00:00:00.000000+00:00","seq":1,"run_id":"run-1","stage":"speech-start","state":"listening","previous_state":"idle"}
{"timestamp":"2026-09-07T00:00:01.000000+00:00","seq":2,"run_id":"run-1","stage":"tts-start","state":"tts-speaking","previous_state":"listening"}
{"timestamp":"2026-09-07T00:00:02.000000+00:00","seq":3,"run_id":"run-1","stage":"interrupt-detected","state":"tts-speaking","previous_state":"tts-speaking"}
{"timestamp":"2026-09-07T00:00:02.001000+00:00","seq":4,"run_id":"run-1","stage":"tts-cancelled","state":"listening","previous_state":"tts-speaking"}
{"timestamp":"2026-09-07T00:00:03.000000+00:00","seq":5,"run_id":"run-1","stage":"speech-end","state":"idle","previous_state":"listening"}`;
const realLines = raw.trim().split("\n").filter(Boolean);
const realEvents = realLines.map(l => JSON.parse(l));

// --- Test 1: parseJsonLine ---
console.log("Test 1: parseJsonLine");
for (const line of realLines) {
  const ev = parseJsonLine(line);
  if (!ev) { console.error("FAILED to parse:", line.slice(0, 60)); process.exit(1); }
}
console.log(`  PASSED: all ${realLines.length} lines parse`);

// --- Test 2: mapAgentState ---
console.log("Test 2: mapAgentState");
const stateTests: [string, string | undefined][] = [
  ["idle", "idle"],
  ["listening", "listening"],
  ["tts-speaking", "speaking"],
  ["tool-pending", "processing"],
  ["tool-running", "processing"],
  ["tool-completed", "processing"],
  ["unknown", "idle"],
];
for (const [input, expected] of stateTests) {
  const got = mapAgentState(input);
  if (got !== expected) {
    console.error(`  FAILED mapAgentState("${input}") => "${got}", expected "${expected}"`);
    process.exit(1);
  }
}
console.log("  PASSED: all state mappings correct");

// --- Test 3: SeqDeduplicator basic ---
console.log("Test 3: SeqDeduplicator basic dedup");
const dedup = new SeqDeduplicator();
let accepted = 0;
let deduplicated = 0;
for (const ev of realEvents) {
  if (dedup.accept(ev)) accepted++;
  else deduplicated++;
}
console.log(`  accepted=${accepted} deduplicated=${deduplicated}`);
// Second pass: all should be dropped
let secondPassAccepted = 0;
for (const ev of realEvents) {
  if (dedup.accept(ev)) secondPassAccepted++;
}
if (secondPassAccepted > 0) {
  console.error(`  FAILED: ${secondPassAccepted} events accepted on second pass`);
  process.exit(1);
}
console.log("  PASSED: duplicate detection works");

// --- Test 4: out-of-order discard ---
console.log("Test 4: out-of-order discard");
const dedup2 = new SeqDeduplicator();
dedup2.accept({ run_id: "test-run", seq: 3 });
const result = dedup2.accept({ run_id: "test-run", seq: 2 });
if (result) {
  console.error("  FAILED: out-of-order seq 2 was not discarded after seq 3");
  process.exit(1);
}
console.log("  PASSED: out-of-order discard works");

// --- Test 5: stage badge colors ---
console.log("Test 5: stageBadgeColor");
const badgeTests: [string, string][] = [
  ["interrupt-detected", "red"],
  ["tts-cancelled", "red"],
  ["tool-cancelled", "red"],
  ["tts-start", "speaking"],
  ["speech-start", "listening"],
  ["tool-pending", "neutral"],
  ["tool-start", "neutral"],
  ["tts-end", "idle"],
  ["speech-end", "idle"],
];
for (const [stage, expected] of badgeTests) {
  const got = stageBadgeColor(stage);
  if (got !== expected) {
    console.error(`  FAILED stageBadgeColor("${stage}") => "${got}", expected "${expected}"`);
    process.exit(1);
  }
}
console.log("  PASSED: badge colors correct");

// --- Test 6: isInterruptDetected ---
console.log("Test 6: isInterruptDetected");
const interruptEv = realEvents.find((e: any) => e.stage === "interrupt-detected");
const nonInterruptEv = realEvents.find((e: any) => e.stage === "speech-start");
if (!isInterruptDetected(interruptEv)) { console.error("  FAILED: interrupt-detected not detected"); process.exit(1); }
if (isInterruptDetected(nonInterruptEv)) { console.error("  FAILED: speech-start falsely detected"); process.exit(1); }
console.log("  PASSED: interrupt detection correct");

// --- Test 7: real event state → visual state ---
console.log("Test 7: Real event states map to correct visual states");
for (const ev of realEvents) {
  const mapped = mapAgentState(ev.state);
  if (ev.state === "tts-speaking" && mapped !== "speaking") { console.error("  FAILED"); process.exit(1); }
  if (ev.state === "listening" && mapped !== "listening") { console.error("  FAILED"); process.exit(1); }
  if (ev.state === "idle" && mapped !== "idle") { console.error("  FAILED"); process.exit(1); }
}
console.log("  PASSED: real events map to correct visual states");

console.log("\n=== ALL 7 TESTS PASSED ===");