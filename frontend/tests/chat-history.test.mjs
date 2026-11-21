import assert from "node:assert/strict";
import test from "node:test";
import {
  buildChatRequest, MAX_BODY_BYTES, MAX_HISTORY_TURNS, MAX_QUESTION_CHARS, MAX_TURN_CHARS,
} from "../src/lib/chat-history.ts";

const bytes = (value) => new TextEncoder().encode(JSON.stringify(value)).byteLength;
const turns = (count, content = "hello") =>
  Array.from({ length: count }, (_, index) => ({ role: index % 2 ? "assistant" : "user", content }));

test("keeps only the newest turns, in order", () => {
  const transcript = turns(10).map((turn, index) => ({ ...turn, content: `turn ${index}` }));
  const request = buildChatRequest("Next?", transcript, "day");
  assert.equal(request.history.length, MAX_HISTORY_TURNS);
  assert.deepEqual(request.history.map((turn) => turn.content), ["turn 4", "turn 5", "turn 6", "turn 7", "turn 8", "turn 9"]);
  assert.deepEqual(request, { ...request, message: "Next?", period: "day", date: null });
});

test("trims whitespace and clips the question and each turn", () => {
  const request = buildChatRequest(`  ${"q".repeat(900)}  `, [{ role: "assistant", content: "a".repeat(5000) }], "week");
  assert.equal(request.message.length, MAX_QUESTION_CHARS);
  assert.equal(request.history[0].content.length, MAX_TURN_CHARS);
});

test("drops empty turns", () => {
  const request = buildChatRequest("Hi", [{ role: "user", content: "   " }], "day");
  assert.deepEqual(request.history, []);
});

test("never splits an emoji when clipping", () => {
  const request = buildChatRequest("😀".repeat(MAX_QUESTION_CHARS + 5), [], "day");
  assert.equal(Array.from(request.message).length, MAX_QUESTION_CHARS);
  assert.ok(!/[\ud800-\udbff](?![\udc00-\udfff])/.test(request.message));
});

test("drops the oldest turns until multi-byte text fits the proxy body budget", () => {
  const request = buildChatRequest("问题".repeat(250), turns(6, "能源".repeat(1000)), "month");
  assert.ok(bytes(request) <= MAX_BODY_BYTES);
  assert.ok(request.history.length < 6 && request.history.length > 0);
  assert.equal(request.history.at(-1).content, "能源".repeat(1000));
});
