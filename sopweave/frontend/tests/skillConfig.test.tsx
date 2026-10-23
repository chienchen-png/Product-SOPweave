import React from "react";
import test, { afterEach } from "node:test";
import assert from "node:assert/strict";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  cleanup,
  act,
} from "@testing-library/react";
import BuiltinSkillDetail from "../src/components/skills/BuiltinSkillDetail";
import SkillCreateWizard from "../src/components/skills/SkillCreateWizard";

const originalFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});
const policy = {
  currency: "CNY",
  decimal_places: 2,
  tolerance: "0.01",
  required_inputs: [],
  require_approval_for: [],
  write_paths: [],
  source_versions: [],
  amount_fields: [],
  checks: [],
};
const spec = {
  schema_version: 1,
  title: "月末核对",
  description: "核对流水",
  inputs: [],
  outputs: ["报告.md"],
  required_tools: ["read_text"],
  steps: [
    {
      id: "one",
      title: "读取",
      instruction: "读取业务来源",
      tool_ids: ["read_text"],
    },
  ],
  execution_policy: policy,
  advisory_rules: [],
  blockers: [],
};
const detail: any = {
  name: "data-reconcile",
  description: "核对流水",
  steps: [],
  capabilities: { models: [], stats: [], charts: [] },
  trigger_words: [],
  toolsets: [],
  body: "读取业务来源",
  constraints: "",
  origin: "builtin",
  readonly: true,
};
const draft: any = {
  draft_id: "draft-one",
  draft_revision: 2,
  state: "ready",
  spec,
  source: [],
  questions: [],
  answers: [],
  generated_digest: "digest",
  preview: {
    ...detail,
    title: spec.title,
    origin: "user",
    readonly: false,
    required_tools: spec.required_tools,
    steps: spec.steps,
    inputs: [],
    outputs: [],
    execution_policy: policy,
  },
};
const response = (data: unknown, status = 200) =>
  new Response(
    JSON.stringify({
      success: status === 200,
      data,
      message: status === 409 ? "版本已变化" : "",
    }),
    { status },
  );

test("builtin_actions_are_absent", () => {
  render(<BuiltinSkillDetail detail={detail} />);
  assert.equal(screen.queryByRole("button", { name: /编辑|删除/ }), null);
  assert.ok(screen.getByText("内置 · 只读"));
});

test("builtin_workflow_renders_as_a_visual_flow", () => {
  render(<BuiltinSkillDetail detail={{
    ...detail,
    steps: [
      { n: 1, title: "读取数据", summary: "读取业务来源", branch_point: false, branches: [] },
      { n: 2, title: "核对差异", summary: "按规则核对金额", branch_point: true, branches: [{ key: "A", title: "一致", summary: "" }, { key: "B", title: "差异", summary: "" }] },
      { n: 3, title: "输出报告", summary: "输出报告并保存文件", branch_point: false, branches: [] },
    ],
  }} />);
  assert.ok(screen.getByRole("list", { name: "SOP 执行流程" }));
  assert.equal(screen.getAllByRole("listitem").length, 3);
  assert.ok(screen.getByText("读取数据"));
  assert.ok(screen.getByText("B · 差异"));
  assert.equal(screen.getAllByText("输出报告并保存文件").length, 1);
});

test("create_requires_current_preview_confirmation_and_edit_invalidates_it", async () => {
  globalThis.fetch = async (input) =>
    response(String(input).endsWith("/tools") ? { catalog: [] } : draft);
  render(
    <SkillCreateWizard
      draftId="draft-one"
      onClose={() => {}}
      onPublished={() => {}}
    />,
  );
  const title = await screen.findByLabelText("SOP 名称");
  const publish = screen.getByRole("button", {
    name: "确认发布",
  }) as HTMLButtonElement;
  assert.equal(publish.disabled, true);
  fireEvent.click(screen.getByLabelText(/我已审阅/));
  assert.equal(publish.disabled, false);
  fireEvent.change(title, { target: { value: "新的名称" } });
  assert.equal(publish.disabled, true);
});

test("conflicting_edit_preserves_user_input", async () => {
  globalThis.fetch = async (input, options) =>
    options?.method === "PATCH"
      ? response(null, 409)
      : response(String(input).endsWith("/tools") ? { catalog: [] } : draft);
  render(
    <SkillCreateWizard
      draftId="draft-one"
      onClose={() => {}}
      onPublished={() => {}}
    />,
  );
  const title = await screen.findByLabelText("SOP 名称");
  fireEvent.change(title, { target: { value: "不能丢失的编辑" } });
  fireEvent.click(screen.getByRole("button", { name: "保存修改并更新预览" }));
  await screen.findByRole("alert");
  assert.equal((title as HTMLInputElement).value, "不能丢失的编辑");
});

test('closing a new SOP saves the first description before dismissing', async () => {
  let closed = false;
  let saved: any;
  globalThis.fetch = async (input, options) => {
    if (String(input).endsWith('/tools')) return response({catalog:[]});
    if (options?.method === 'POST') saved = JSON.parse(String(options.body));
    return response({...draft,state:'draft',spec:null,source:[{kind:'text',text:saved?.text}]});
  };
  render(<SkillCreateWizard onClose={() => { closed = true; }} onPublished={() => {}} />);
  fireEvent.change(screen.getByLabelText('需求描述'), {target:{value:'按合同编号核对银行流水'}});
  fireEvent.click(screen.getByRole('button',{name:'关闭并保留草稿'}));
  await waitFor(() => assert.equal(closed,true));
  assert.equal(saved.text,'按合同编号核对银行流水');
});

test('a failed close-save keeps the SOP editor and user input', async () => {
  let closed = false;
  globalThis.fetch = async (input) => String(input).endsWith('/tools') ? response({catalog:[]}) : response(null,409);
  render(<SkillCreateWizard onClose={() => { closed = true; }} onPublished={() => {}} />);
  fireEvent.change(screen.getByLabelText('需求描述'), {target:{value:'不能丢失的 SOP 需求'}});
  fireEvent.click(screen.getByRole('button',{name:'关闭并保留草稿'}));
  await screen.findByRole('alert');
  assert.equal(closed,false);
  assert.equal((screen.getByLabelText('需求描述') as HTMLTextAreaElement).value,'不能丢失的 SOP 需求');
});

test('closing during automatic save waits for that save without creating a duplicate draft', async () => {
  let closed=false, creates=0, finish:(value:Response)=>void=()=>{};
  globalThis.fetch=async (input,options)=>{
    if(String(input).endsWith('/tools'))return response({catalog:[]});
    if(options?.method==='POST') {creates++;return new Promise<Response>(resolve=>{finish=resolve;});}
    return response(draft);
  };
  render(<SkillCreateWizard onClose={()=>{closed=true;}} onPublished={()=>{}}/>);
  fireEvent.change(screen.getByLabelText('需求描述'),{target:{value:'等待保存的输入'}});
  await waitFor(()=>assert.equal(creates,1),{timeout:2000});
  fireEvent.click(screen.getByRole('button',{name:'关闭并保留草稿'}));
  await act(async()=>finish(response({...draft,state:'created',spec:null,source:[{kind:'text',text:'等待保存的输入'}]})));
  await waitFor(()=>assert.equal(closed,true));
  assert.equal(creates,1);
});
