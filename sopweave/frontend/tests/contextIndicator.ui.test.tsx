import React from "react";
import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ContextIndicator from "../src/components/ContextIndicator";

afterEach(() => {
  cleanup();
});

const stats = {
  current_tokens: 595000,
  context_window: 1000000,
  context_window_source: "configured",
  response_reserve: 150000,
  effective_budget: 850000,
  usage_pct: 70,
  message_count: 4,
};

test("context panel shows only the compact usage summary", async () => {
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ success: true, data: stats }));
  render(<ContextIndicator projectId="p" />);
  const button = await screen.findByTitle(/上下文估算：595,000/);
  fireEvent.click(button);
  assert.ok(screen.getByText("模型窗口"));
  assert.ok(screen.getByText("1,000,000"));
  assert.ok(screen.getByText("估算已用"));
  assert.ok(screen.getByText("595,000（70%）"));
  assert.match(screen.getByText(/共 4 条消息/).textContent || "", /4/);
  assert.equal(screen.queryByText(/输出预留/), null);
  assert.equal(screen.queryByText(/UTF-8/), null);
  assert.equal(screen.getByTestId("context-arc").getAttribute("stroke"), "#d97706");
});

test("failed context reset keeps the panel open and reports the server error", async () => {
  let resets = 0;
  let alerted = "";
  const originalConfirm = window.confirm;
  const originalAlert = window.alert;
  window.confirm = () => true;
  window.alert = (message) => {
    alerted = String(message);
  };
  globalThis.fetch = async (input, options) => {
    if (options?.method === "POST") {
      resets++;
      return new Response(
        JSON.stringify({ success: false, message: "项目任务仍在运行" }),
        { status: 409 },
      );
    }
    return new Response(JSON.stringify({ success: true, data: stats }));
  };
  try {
    render(<ContextIndicator projectId="p" />);
    fireEvent.click(await screen.findByTitle(/上下文估算/));
    fireEvent.click(screen.getByRole("button", { name: "清空上下文" }));
    await waitFor(() => assert.equal(resets, 1));
    await waitFor(() => assert.match(alerted, /清空失败.*项目任务仍在运行/));
    assert.ok(screen.getByRole("button", { name: "清空上下文" }));
  } finally {
    window.confirm = originalConfirm;
    window.alert = originalAlert;
  }
});

test('cancelling context confirmation sends no reset and retains the current estimate',async()=>{
  const originalConfirm=window.confirm;
  let resets=0,cleared=0;
  window.confirm=()=>false;
  globalThis.fetch=async(_url,options)=>{
    if(options?.method==='POST') resets++;
    return new Response(JSON.stringify({success:true,data:stats}));
  };
  try{
    render(<ContextIndicator projectId="p" onCleared={()=>cleared++}/>);
    fireEvent.click(await screen.findByTitle(/上下文估算：595,000/));
    fireEvent.click(screen.getByRole('button',{name:'清空上下文'}));
    assert.equal(resets,0);
    assert.equal(cleared,0);
    assert.ok(screen.getByRole('button',{name:'清空上下文'}));
    assert.ok(screen.getByTitle(/上下文估算：595,000/));
  }finally{window.confirm=originalConfirm;}
});

test("context warning colors switch at the documented 60 and 80 percent thresholds", async () => {
  for (const [percentage, color] of [
    [59, "#3f3f46"],
    [60, "#d97706"],
    [80, "#dc2626"],
    [95, "#dc2626"],
  ] as const) {
    globalThis.fetch = async () =>
      new Response(
        JSON.stringify({
          success: true,
          data: { ...stats, usage_pct: percentage },
        }),
      );
    const view = render(<ContextIndicator projectId={`p-${percentage}`} />);
    await screen.findByTitle(/上下文估算/);
    assert.equal(screen.getByTestId("context-arc").getAttribute("stroke"), color);
    view.unmount();
  }
});

test('reset rejects HTTP failures, business failures and malformed success responses',async()=>{
  const confirm=window.confirm,alert=window.alert;
  window.confirm=()=>true;
  try{
    for(const [status,body] of [[500,'{"success":false,"message":"存储失败"}'],[200,'{"success":false,"message":"未能清空"}'],[200,'not-json']] as const){
      let cleared=0,alerted='';
      window.alert=message=>{alerted=String(message);};
      globalThis.fetch=async(_url,options)=>options?.method==='POST'
        ?new Response(body,{status})
        :new Response(JSON.stringify({success:true,data:stats}));
      const view=render(<ContextIndicator projectId="p" onCleared={()=>cleared++}/>);
      fireEvent.click(await screen.findByTitle(/上下文估算/));
      fireEvent.click(screen.getByRole('button',{name:'清空上下文'}));
      await waitFor(()=>assert.match(alerted,/清空失败/));
      assert.equal(cleared,0);
      assert.ok(screen.getByRole('button',{name:'清空上下文'}));
      view.unmount();
    }
  }finally{window.confirm=confirm;window.alert=alert;}
});
