import test from "node:test";
import assert from "node:assert/strict";
import { parseAgentEventStream } from "../.test-build/agentStream.js";

const encoder = new TextEncoder();

function streamFrom(chunks) {
  return new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk));
      controller.close();
    },
  });
}

function frame(seq, type, payload) {
  const event = { run_id: "r1", seq, type, payload, created_at: "now" };
  return `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify(event)}\n\n`;
}

test("parses split frames and UTF-8 characters split across chunks", async () => {
  const bytes = encoder.encode(frame(1, "message.delta", { delta: "你好" }));
  const marker = bytes.findIndex((byte) => byte > 127);
  const text = [];
  await parseAgentEventStream(streamFrom([
    bytes.slice(0, marker + 1),
    bytes.slice(marker + 1, marker + 3),
    bytes.slice(marker + 3),
  ]), { onText: (delta) => text.push(delta) });
  assert.deepEqual(text, ["你好"]);
});

test('replayed events at or below the history cursor are ignored',async()=>{
  const text=[];
  await parseAgentEventStream(streamFrom([encoder.encode(frame(5,'message.delta',{delta:'old'})+frame(6,'message.delta',{delta:'new'})+frame(7,'run.completed',{}))]),{onText:value=>text.push(value)},undefined,5);
  assert.deepEqual(text,['new']);
});

test('reattaching uses the persisted event cursor without starting another model request',async()=>{
  const {watchAgentRun}=await import('../.test-build/agentStream.js');
  const original=globalThis.fetch;const urls=[];const text=[];
  globalThis.fetch=async(url,options)=>{
    urls.push(String(url));assert.equal(options.method,undefined);
    return new Response(streamFrom([encoder.encode(frame(6,'message.delta',{delta:'continued'})+frame(7,'run.completed',{}))]));
  };
  try{await watchAgentRun('r1',5,{onText:value=>text.push(value)});}
  finally{globalThis.fetch=original;}
  assert.deepEqual(urls,['/api/agent/runs/r1/stream?after_seq=5']);assert.deepEqual(text,['continued']);
});

test("parses multiple frames per chunk and ignores heartbeats", async () => {
  const events = [];
  const chunk = encoder.encode(
    `: heartbeat\n\n${frame(1, "run.created", {})}${frame(2, "message.delta", { delta: "A" })}`,
  );
  await parseAgentEventStream(streamFrom([chunk]), {
    onEvent: (event) => events.push(event.type),
  });
  assert.deepEqual(events, ["run.created", "message.delta"]);
});

test("ignores duplicate seq and safely forwards unknown events", async () => {
  const text = [];
  const events = [];
  await parseAgentEventStream(streamFrom([encoder.encode(
    frame(4, "message.delta", { delta: "once" }) +
    frame(4, "message.delta", { delta: "twice" }) +
    frame(5, "future.event", { value: 1 }),
  )]), {
    onText: (delta) => text.push(delta),
    onEvent: (event) => events.push(event.type),
  });
  assert.deepEqual(text, ["once"]);
  assert.deepEqual(events, ["message.delta", "future.event"]);
});

test("maps failed terminal events to one error and one terminal callback", async () => {
  const errors = [];
  const done = [];
  await parseAgentEventStream(streamFrom([encoder.encode(
    frame(8, "run.failed", { message: "模型认证失败" }) +
    frame(9, "message.delta", { delta: "late" }),
  )]), {
    onError: (message) => errors.push(message),
    onDone: (status) => done.push(status),
    onText: () => assert.fail("terminal event must stop later dispatch"),
  });
  assert.deepEqual(errors, ["模型认证失败"]);
  assert.deepEqual(done, ["failed"]);
});


test('tool completion forwards server execution duration',async()=>{
  let completed;
  await parseAgentEventStream(streamFrom([encoder.encode(frame(1,'tool.completed',{tool_id:'timed',output:{success:true},duration_ms:275}))]),{onToolEnd:event=>{completed=event;}});
  assert.equal(completed.duration_ms,275);
});
