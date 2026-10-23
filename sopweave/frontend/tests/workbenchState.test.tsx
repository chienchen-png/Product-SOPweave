import React from 'react';
import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import {useDraftState} from '../src/hooks/useDraftState';
import ReconWizard from '../src/components/ReconWizard';
import Dialog from '../src/components/ui/Dialog';
import NavSidebar from '../src/layout/NavSidebar';
import {FeedbackProvider} from '../src/components/ui/Feedback';
import ModelPicker from '../src/components/ModelPicker';
import MarkdownBlock from '../src/components/MarkdownBlock';
import {useModelSelection} from '../src/hooks/useModelSelection';
import type {ModelOption} from '../src/lib/api';
import {CHAT_BOTTOM_THRESHOLD,isNearScrollBottom} from '../src/lib/chatScroll';

afterEach(() => { cleanup(); window.localStorage.clear(); });
test('chat auto-follow pauses once the reader scrolls above the bottom threshold',()=>{
  assert.equal(isNearScrollBottom({scrollHeight:2000,scrollTop:1128,clientHeight:800}),true);
  assert.equal(isNearScrollBottom({scrollHeight:2000,scrollTop:1127,clientHeight:800}),false);
  assert.equal(CHAT_BOTTOM_THRESHOLD,72);
});
test('chat markdown renders tables, code and block formulas together',()=>{
  const {container}=render(<MarkdownBlock text={'| 项目 | 金额 |\n| --- | ---: |\n| 收入 | 100 |\n\n```python\nprint("ok")\n```\n\n$$E=mc^2$$'} />);
  assert.equal(container.querySelectorAll('table').length,1);
  assert.equal(container.querySelectorAll('pre code').length,1);
  assert.equal(container.querySelectorAll('.katex').length,1);
});
test('a deleted selected model stays unavailable across registry refreshes until explicit reselection',()=>{
  const first={id:'first',name:'模型甲',provider_id:'provider',type:'chat',is_default:0} as ModelOption;
  const other={id:'other',name:'模型乙',provider_id:'provider',type:'chat',is_default:1} as ModelOption;
  function Page({models,scope='p',preferred='first'}:{models:ModelOption[];scope?:string;preferred?:string}){
    const [id,setId]=useModelSelection(models,preferred,scope);
    return <><output aria-label="所选模型ID">{id}</output><ModelPicker models={models} providers={[{id:'provider',name:'供应商'}]} value={id} onChange={m=>setId(m.id)}/></>;
  }
  const page=render(<Page models={[first,other]}/>);
  page.rerender(<Page models={[other]}/>);
  assert.equal(screen.getByLabelText('所选模型ID').textContent,'first');
  assert.match(screen.getByRole('alert').textContent||'',/模型不可用/);
  page.rerender(<Page models={[{...other}]}/>);
  assert.equal(screen.getByLabelText('所选模型ID').textContent,'first');
  fireEvent.click(screen.getByRole('button',{name:/模型不可用/}));
  fireEvent.click(screen.getByRole('button',{name:/模型乙/}));
  assert.equal(screen.getByLabelText('所选模型ID').textContent,'other');
  assert.equal(!!screen.queryByRole('alert'),false);
  page.rerender(<Page models={[other]} scope="hidden" preferred="missing-hidden"/>);
  assert.equal(screen.getByLabelText('所选模型ID').textContent,'missing-hidden');
  assert.match(screen.getByRole('alert').textContent||'',/模型不可用/);
});
function Draft({id}: {id:string}) {
  const [value,setValue] = useDraftState(id,{text:''});
  return <input aria-label="草稿" value={value.text} onChange={e => setValue({text:e.target.value})} />;
}
test('drafts survive unmount and stay isolated when the scope changes', () => {
  const app=render(<Draft id="one" />);
  fireEvent.change(screen.getByLabelText('草稿'),{target:{value:'项目一'}});
  app.rerender(<Draft id="two" />);
  assert.equal((screen.getByLabelText('草稿') as HTMLInputElement).value,'');
  fireEvent.change(screen.getByLabelText('草稿'),{target:{value:'项目二'}});
  app.unmount();
  render(<Draft id="one" />);
  assert.equal((screen.getByLabelText('草稿') as HTMLInputElement).value,'项目一');
});

test('an empty reconciliation wizard reports no completed input stages', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({success:true,data:{files:[]}}));
  let validity: boolean[] = [];
  render(<ReconWizard projectId="empty" step={1} onStepChange={() => {}} onStart={() => {}} onValidityChange={value => {validity=value;}} />);
  await screen.findByText('选择数据文件');
  assert.deepEqual(validity.slice(0,3),[false,false,false]);
});

test('modal focus enters the dialog and returns to its opener after Escape', async () => {
  function Page() {
    const [open,setOpen]=React.useState(false);
    return <><button onClick={() => setOpen(true)}>打开编辑</button><input aria-label="背景输入"/>{open && <Dialog title="编辑" onClose={() => setOpen(false)}><input aria-label="窗口输入"/></Dialog>}</>;
  }
  render(<Page/>);
  const opener=screen.getByRole('button',{name:'打开编辑'});
  opener.focus(); fireEvent.click(opener);
  await waitFor(() => assert.ok(screen.getByRole('dialog').contains(document.activeElement)));
  fireEvent.keyDown(document.activeElement!,{key:'Escape',code:'Escape'});
  await waitFor(() => assert.equal(screen.queryByRole('dialog'),null));
  assert.equal(document.activeElement,opener);
});

test('collapsed navigation keeps accessible project and SOP actions', () => {
  Object.defineProperty(window, 'innerWidth', {value:1366,writable:true,configurable:true});
  let selected=''; let view='';
  render(<FeedbackProvider><NavSidebar projects={[{id:'p-one',name:'银行核对'} as any]} activeProjectId={null} activeView="dashboard" onSelectProject={id => {selected=id;}} onNewProject={() => {}} onNavigate={next => {view=next;}} onDeleteProject={() => {}} onDuplicateProject={() => {}} onRenameProject={() => {}}/></FeedbackProvider>);
  fireEvent.click(screen.getByRole('button',{name:'收起导航'}));
  fireEvent.click(screen.getByRole('button',{name:'银行核对'}));
  assert.equal(selected,'p-one');
  fireEvent.click(screen.getByRole('button',{name:'财务 SOP'}));
  assert.equal(view,'sops');
});

test('project copying explains the shared directory before creating an independent dataset snapshot', async () => {
  const calls: string[]=[];let copied='';
  globalThis.fetch=async (url,options)=>{
    if(options?.method)calls.push(String(url));
    return new Response(JSON.stringify({success:true,data:{id:'copy',name:'银行核对 副本',work_dir:'D:\\Audit Shared'}}));
  };
  render(<FeedbackProvider><NavSidebar projects={[{id:'source',name:'银行核对',work_dir:'D:\\Audit Shared'} as any]} activeProjectId="source" activeView="chat" onSelectProject={()=>{}} onNewProject={()=>{}} onNavigate={()=>{}} onDeleteProject={()=>{}} onDuplicateProject={p=>{copied=p.id;}} onRenameProject={()=>{}}/></FeedbackProvider>);
  fireEvent.click(screen.getByRole('button',{name:'项目操作'}));
  fireEvent.click(screen.getByRole('button',{name:'创建副本'}));
  assert.ok(await screen.findByText(/共用工作目录/));
  assert.ok(screen.getByText(/D:\\Audit Shared/));
  assert.equal(calls.length,0);
  fireEvent.click(screen.getByRole('button',{name:'取消',exact:true}));
  assert.equal(calls.length,0);
  fireEvent.click(screen.getByRole('button',{name:'创建副本'}));
  fireEvent.click(await screen.findByRole('button',{name:'确认',exact:true}));
  await waitFor(()=>assert.equal(copied,'copy'));
  assert.deepEqual(calls,['/api/projects/source/duplicate']);
});
