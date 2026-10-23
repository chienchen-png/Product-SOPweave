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
import SopPicker from "../src/components/skills/SopPicker";
import { notifySkillsChanged } from "../src/lib/skillEvents";

const originalFetch = globalThis.fetch;
afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});
const skill = {
  name: "user-one",
  title: "月度核对",
  description: "核对",
  trigger_words: [],
  step_count: 1,
  path: "",
};
const response = (data: unknown, status = 200) =>
  new Response(
    JSON.stringify({ success: status === 200, data, message: "已删除" }),
    { status },
  );

test("auto_none_explicit_map_to_valid_selection", async () => {
  globalThis.fetch = async () => response({ skills: [skill] });
  let result: any;
  render(
    <SopPicker
      value={{ mode: "auto", skill_id: "" }}
      onChange={(value) => {
        result = value;
      }}
    />,
  );
  fireEvent.click(screen.getByLabelText('选择本次任务的 SOP'));
  await screen.findByRole('button', {name:'月度核对'});
  fireEvent.click(screen.getByRole('button',{name:'仅内置流程'}));
  assert.deepEqual(result, { mode: "none", skill_id: "", inputs: {} });
  fireEvent.click(screen.getByLabelText('选择本次任务的 SOP'));
  fireEvent.click(await screen.findByRole('button',{name:'月度核对'}));
  assert.equal(result.mode, "explicit");
  assert.equal(result.skill_id, "user-one");
});

test('SOP search can load the next page and selecting a required-input SOP exposes validation', async () => {
  globalThis.fetch = async input => {
    const url=String(input);
    if(url.includes('/user-two')) return response({...skill,name:'user-two',title:'凭证检查',version:1,steps:[],inputs:[{name:'period',description:'核对期间',required:true,default:''}]});
    return response(url.includes('cursor=') ? {skills:[{...skill,name:'user-two',title:'凭证检查'}]} : {skills:[skill],next_cursor:'user-one'});
  };
  function Picker() {
    const [value,setValue]=React.useState<any>({mode:'auto',skill_id:''});
    const [error,setError]=React.useState('');
    return <><SopPicker value={value} onChange={setValue} onValidationChange={setError}/><p data-testid="validation">{error}</p></>;
  }
  render(<Picker/>);
  fireEvent.click(screen.getByLabelText('选择本次任务的 SOP'));
  fireEvent.click(await screen.findByRole('button',{name:'加载更多 SOP'}));
  fireEvent.click(await screen.findByRole('button',{name:'凭证检查'}));
  await waitFor(() => assert.match(screen.getByTestId('validation').textContent||'',/period/));
  fireEvent.change(screen.getByLabelText('SOP 参数 period'),{target:{value:'2026-10'}});
  await waitFor(() => assert.equal(screen.getByTestId('validation').textContent,''));
});

test('a recovered SOP search clears its earlier network error', async () => {
  let failing=true;
  globalThis.fetch = async () => failing ? response(null,503) : response({skills:[skill]});
  render(<SopPicker value={{mode:'auto',skill_id:''}} onChange={() => {}}/>);
  fireEvent.click(screen.getByLabelText('选择本次任务的 SOP'));
  await screen.findByRole('alert');
  failing=false;
  fireEvent.click(screen.getByRole('button',{name:'重试'}));
  await screen.findByRole('button',{name:'月度核对'});
  assert.equal(screen.queryByRole('alert'),null);
});

test("deleted_selection_requires_reselection_without_auto_switch", async () => {
  globalThis.fetch = async (input) =>
    String(input).includes("/user-one")
      ? response(null, 404)
      : response({ skills: [] });
  let result: any;
  render(
    <SopPicker
      value={{ mode: "explicit", skill_id: "user-one" }}
      onChange={(value) => {
        result = value;
      }}
    />,
  );
  await waitFor(() =>
    assert.deepEqual(result, { mode: "explicit", skill_id: "", inputs: {} }),
  );
});

test("busy_run_does_not_change_its_selection_on_catalog_event", async () => {
  globalThis.fetch = async (input) =>
    String(input).includes("/user-one")
      ? response({ ...skill, version: 2, steps: [] })
      : response({ skills: [skill] });
  let changed = false;
  render(
    <SopPicker
      value={{ mode: "explicit", skill_id: "user-one" }}
      disabled
      onChange={() => {
        changed = true;
      }}
    />,
  );
  act(() => notifySkillsChanged());
  await waitFor(() => assert.match(screen.getByLabelText("选择本次任务的 SOP").title,/v2/));
  assert.equal(changed, false);
  assert.equal(
    (screen.getByLabelText("选择本次任务的 SOP") as HTMLButtonElement).disabled,
    true,
  );
});
