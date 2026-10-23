import React from 'react';
import test, { afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import AiConfigPage, { AgentConfigPanel } from '../src/pages/AiConfigPage';
import SopLibraryPage from '../src/pages/SopLibraryPage';
import { FeedbackProvider } from '../src/components/ui/Feedback';
import AgentToolCatalog from '../src/components/AgentToolCatalog';
import SkillConfigPanel from '../src/components/SkillConfigPanel';
import type { AgentDetail, ToolCategory } from '../src/lib/api';
import { updateModel } from '../src/lib/api';
import NewProjectDialog from '../src/components/NewProjectDialog';
import { AiRegistryProvider, useAiRegistry } from '../src/context/AiRegistryContext';
import { MemoryRouter } from 'react-router-dom';

const originalFetch = globalThis.fetch;
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; });

test('project model menu closes when another field inside its dialog is clicked', async () => {
  globalThis.fetch = async input => new Response(JSON.stringify({ success: true, data:
    String(input).includes('/api/options') ? { models: [{ id: 'model', name: '测试模型', type: 'chat', is_default: 1 }], agents: [] } : [],
  }));
  render(<AiRegistryProvider><NewProjectDialog onCancel={() => {}} onCreate={() => {}}/></AiRegistryProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '默认模型 测试模型' }));
  assert.ok(screen.getByRole('button', { name: /^测试模型\s*默认$/ }));
  fireEvent.click(screen.getByRole('textbox', { name: /^项目名称/ }));
  assert.equal(screen.queryByRole('button', { name: /^测试模型\s*默认$/ }) === null, true);
});

test('project creation exposes named fields and guards a pending submission', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ success: true, data: { models: [], agents: [] } }));
  let submitted = 0;
  let finish!: () => void;
  render(<AiRegistryProvider><NewProjectDialog onCancel={() => {}} onCreate={async () => {
    submitted++;
    await new Promise<void>(resolve => { finish = resolve; });
  }}/></AiRegistryProvider>);
  const name = screen.getByRole('textbox', { name: /^项目名称/ });
  const directory = screen.getByRole('textbox', { name: /^工作目录/ });
  fireEvent.change(name, { target: { value: '  验收项目  ' } });
  fireEvent.change(directory, { target: { value: 'C:/验收 目录' } });
  const create = screen.getByRole('button', { name: '创建', exact: true });
  fireEvent.click(create);
  fireEvent.click(create);
  await waitFor(() => assert.equal(submitted, 1));
  assert.equal((create as HTMLButtonElement).disabled, true);
  finish();
  await waitFor(() => assert.equal((create as HTMLButtonElement).disabled, false));
});

const detail: AgentDetail = {
  id: 'general-agent', name: '通用智能体', version: '4.0.0', enabled: true,
  role: '财务助手', description: '财务助手', default_model_id: '', toolsets: ['core', 'office'],
  tools: [], skills: ['office-files'], approval_policy: { run_process: 'always' },
  max_turns: 30, progressive_tools: true, system_prompt: '# 当前指令',
};
const catalog: ToolCategory[] = [
  { category: 'core', tools: [{ name: 'read_text', description: '读取文本', risk: 'read', loading: 'default' }] },
  { category: 'office', tools: [{ name: 'edit_document', description: '编辑 Word', risk: 'write', loading: 'on_demand' }] },
];

test('invalid Agent diagnostics remain visible alongside usable configuration and when all are invalid', () => {
  const errors = [{agent_id:'broken-agent',path:'broken-agent/agent.yaml',message:'无法解析智能体配置'}];
  const view = render(<AgentConfigPanel detail={detail} catalog={catalog} definitionErrors={errors} onSaved={() => {}}/>);
  assert.match(screen.getByRole('alert').textContent || '', /broken-agent\/agent.yaml/);
  assert.ok(screen.getByRole('button', {name:'编辑定义'}));
  view.rerender(<AgentConfigPanel detail={null} catalog={[]} definitionErrors={errors} onSaved={() => {}}/>);
  assert.ok(screen.getByText('没有可用智能体，请修正下列定义文件后刷新。'));
  assert.ok(screen.getByRole('alert'));
});

test('Agent page refresh reloads registry diagnostics after files change', async () => {
  let broken = false;
  globalThis.fetch = async input => {
    const url = String(input);
    const data = url.endsWith('/api/options') ? {
      models:[], agents:[detail], agent_definition_errors:broken ? [{agent_id:'broken-agent',path:'broken-agent/agent.yaml',message:'invalid YAML'}] : [],
    } : url.endsWith('/general-agent/tools') ? {catalog}
      : url.endsWith('/general-agent') ? {agent:detail} : [];
    return new Response(JSON.stringify({success:true,data}));
  };
  render(<FeedbackProvider><AiRegistryProvider><AiConfigPage tab="agent"/></AiRegistryProvider></FeedbackProvider>);
  assert.equal(screen.queryByRole('button', { name: 'Skill 配置' }), null);
  await screen.findByText('编辑定义');
  broken = true;
  fireEvent.click(screen.getByTitle('刷新'));
  await screen.findByText('invalid YAML');
  assert.match(screen.getByRole('alert').textContent || '', /broken-agent\/agent.yaml/);
  broken = false;
  fireEvent.click(screen.getByTitle('刷新'));
  await waitFor(() => assert.ok(!screen.queryByRole('alert')));
});

test('registry refreshes after another tab changes configuration and on focus', async () => {
  const originalChannel = window.BroadcastChannel;
  const listeners = new Set<() => void>();
  let closed = 0;
  let broadcasts = 0;
  (window as any).BroadcastChannel = class {
    addEventListener(_type: string, listener: () => void) { listeners.add(listener); }
    postMessage() { broadcasts++; }
    close() { closed++; listeners.clear(); }
  };
  let name = 'initial-model';
  let failed = false;
  globalThis.fetch = async input => new Response(JSON.stringify({success:!failed, message:failed ? 'conflict' : undefined, data:
    String(input).endsWith('/api/options') ? {models:[{id:'stable-id',name}],agents:[]} : [],
  }), {status:failed ? 409 : 200});
  function Probe() { const {models} = useAiRegistry(); return <span>{models[0]?.name || 'loading'}</span>; }
  try {
    const view = render(<AiRegistryProvider><Probe/></AiRegistryProvider>);
    await screen.findByText('initial-model');
    assert.equal(listeners.size, 1);
    await updateModel('stable-id', {name:'new display name'});
    assert.equal(broadcasts, 1);
    failed = true;
    await assert.rejects(updateModel('stable-id', {name:'rejected'}), /conflict/);
    assert.equal(broadcasts, 1, 'failed mutations must not broadcast success');
    failed = false;
    name = 'renamed-in-other-tab';
    await act(async () => { listeners.forEach(listener => listener()); });
    await screen.findByText(name);
    name = 'updated-while-in-background';
    fireEvent(window, new window.Event('focus'));
    await screen.findByText(name);
    view.unmount();
    assert.equal(closed, 1);
  } finally { (window as any).BroadcastChannel = originalChannel; }
});

test('a slower previous registry response cannot overwrite a newer refresh',async()=>{
  let calls=0;
  let releasePrevious!:()=>void;
  const response=(name:string)=>new Response(JSON.stringify({success:true,data:{models:[{id:'stable',name}],agents:[]}}));
  globalThis.fetch=async input=>{
    if(!String(input).endsWith('/api/options')) return new Response(JSON.stringify({success:true,data:[]}));
    if(++calls===1) return new Promise<Response>(resolve=>{releasePrevious=()=>resolve(response('旧响应'));});
    return response('最新响应');
  };
  function Probe(){const {models,refresh}=useAiRegistry();return <><span>{models[0]?.name}</span><button onClick={()=>void refresh()}>刷新探针</button></>;}
  render(<AiRegistryProvider><Probe/></AiRegistryProvider>);
  fireEvent.click(screen.getByRole('button',{name:'刷新探针'}));
  await screen.findByText('最新响应');
  await act(async()=>{releasePrevious();});
  assert.ok(screen.getByText('最新响应'));
  assert.ok(!screen.queryByText('旧响应'));
});

test('catalog renders server-provided tool counts, loading modes and risks', () => {
  render(<AgentToolCatalog catalog={catalog}/>);
  assert.ok(screen.getByText('可用 2 个 · 默认加载 1 个 · 按需加载 1 个'));
  assert.ok(screen.getByText('edit_document'));
  assert.ok(screen.getByText('Excel 与 Word（1）'));
  assert.ok(screen.getByText('文件写入'));
});

test('editing Agent instructions preserves progressive loading and current runtime settings', async () => {
  let manifest: any;
  let saved = false;
  globalThis.fetch = async (_input, options) => {
    manifest = JSON.parse(String(options?.body)).manifest;
    return new Response(JSON.stringify({ success: true, data: { agent: detail } }));
  };
  render(<AgentConfigPanel detail={detail} catalog={catalog} onSaved={() => { saved = true; }}/>);
  assert.ok(screen.getByText('30'));
  assert.equal(screen.queryByText('无上限'), null);
  assert.equal(screen.queryByText('上下文策略'), null);
  fireEvent.click(screen.getByRole('button', { name: '编辑定义' }));
  fireEvent.click(screen.getByRole('button', { name: '校验并保存' }));
  await waitFor(() => assert.equal(saved, true));
  assert.equal(manifest.progressive_tools, true);
  assert.equal(manifest.max_turns, 30);
  assert.deepEqual(manifest.approval_policy, { run_process: 'always' });
});

test('Skill without legacy step headings shows its actual Markdown and activation groups', async () => {
  globalThis.fetch = async input => new Response(JSON.stringify({ success: true, data:
    new URL(String(input), 'http://localhost').pathname.endsWith('/office-files') ? {
      name: 'office-files', description: 'Office', user_invocable: true, trigger_words: [], steps: [],
      constraints: '', capabilities: { models: [], stats: [], charts: [] },
      content: '---\nname: office-files\n---\n使用 read_document 读取文档。',
      body: '使用 `read_document` 读取文档。', toolsets: ['office'],
    } : { skills: [{ name: 'office-files', description: 'Office', trigger_words: [], step_count: 0, path: 'skills/office-files/SKILL.md' }] },
  }));
  render(<MemoryRouter><SkillConfigPanel/></MemoryRouter>);
  assert.ok(await screen.findByText('read_document'));
  assert.ok(screen.getByText('Excel 与 Word'));
  assert.equal(screen.queryByText('0 步'), null);
});

test('SOP management exposes authoring actions without enable or disable controls', async () => {
  const skillId = 'user-' + 'a'.repeat(32);
  globalThis.fetch = async input => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/api/skills') {
      return new Response(JSON.stringify({ success: true, data: {
        skills: [{
          name: skillId, title: '月度关账', description: '月结流程',
          origin: 'user', readonly: false, version: 1, step_count: 1,
          enabled: true, trigger_words: [], path: `skills/${skillId}/SKILL.md`,
        }],
        generation: 7, user_skills_enabled: true, next_cursor: '',
      }}));
    }
    return new Response(JSON.stringify({ success: true, data: {
      name: skillId, title: '月度关账', description: '月结流程',
      origin: 'user', readonly: false, version: 1, enabled: true,
      user_invocable: true, trigger_words: [], steps: [],
      constraints: '', capabilities: { models: [], stats: [], charts: [] },
      content: '', body: '', toolsets: [], required_tools: [],
      inputs: [], outputs: [], advisory_rules: [],
    }}));
  };
  render(<MemoryRouter><SkillConfigPanel/></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: '用户 SOP' }));
  assert.ok(await screen.findByRole('button', { name: '编辑 SOP' }));
  assert.ok(screen.getByRole('button', { name: '删除 SOP' }));
  assert.equal(screen.queryByRole('button', { name: '启用 SOP' }), null);
  assert.equal(screen.queryByRole('button', { name: '停用 SOP' }), null);
  assert.equal(screen.queryByText('财务 SOP 流程库'), null);
});

test('SOP library uses one page heading and has no integration shortcut cards', async () => {
  globalThis.fetch = async input => {
    const path = new URL(String(input), 'http://localhost').pathname;
    const data = path === '/api/options'
      ? { models: [], agents: [] }
      : path === '/api/skills'
        ? { skills: [], user_skills_enabled: true, next_cursor: '', generation: 1 }
        : [];
    return new Response(JSON.stringify({ success: true, data }));
  };
  render(<MemoryRouter><AiRegistryProvider><SopLibraryPage/></AiRegistryProvider></MemoryRouter>);
  assert.equal((await screen.findAllByRole('heading', { name: '财务 SOP' })).length, 1);
  assert.ok(screen.getByRole('button', { name: '刷新' }));
  assert.equal(screen.queryByRole('button', { name: '新增 SOP' }), null);
  assert.equal(screen.queryByRole('button', { name: '数据核对与校验' }), null);
  assert.equal(screen.queryByRole('button', { name: '财务建模与分析' }), null);
  assert.equal(screen.queryByRole('button', { name: '使用此 SOP' }), null);
  assert.equal(screen.queryByText('财务 SOP 流程库'), null);
  fireEvent.click(screen.getByRole('button', { name: '用户 SOP' }));
  const addButtons = await screen.findAllByRole('button', { name: '新增 SOP' });
  assert.equal(addButtons.length, 1);
  fireEvent.click(addButtons[0]);
  assert.ok(screen.getByRole('menuitem', { name: '新建 SOP' }));
  assert.ok(screen.getByRole('menuitem', { name: '导入 GitHub / ZIP' }));
});
