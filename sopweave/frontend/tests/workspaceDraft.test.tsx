import React from 'react';
import test,{afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {render,screen,fireEvent,cleanup,waitFor,renderHook} from '@testing-library/react';
import {useWorkspaceDraft} from '../src/hooks/useWorkspaceDraft';
import {invalidateFinmod,invalidateRecon,restoreFinmodTask} from '../src/lib/workflowDrafts';

afterEach(()=>{cleanup();window.localStorage.clear();});
const response=(data:unknown,status=200)=>new Response(JSON.stringify({success:status===200,data,message:'草稿版本冲突'}),{status});
function Editor({project='one'}:{project?:string}){
  const draft=useWorkspaceDraft<Record<string,unknown>>(project,'finmod',{});
  return <><input aria-label="工作草稿" disabled={!draft.ready} value={String(draft.value.goal||'')} onChange={e=>draft.setValue(value=>({...value,goal:e.target.value}))}/>{draft.error&&<p role="alert">{draft.error}</p>}<span>{draft.saving?'保存中':draft.dirty?'未保存':'已保存'}</span></>;
}
test('server drafts restore after clearing browser storage',async()=>{
  let state:any={revision:0,state:{},data_revision:'data',saved_data_revision:'data'};
  globalThis.fetch=async (input,options)=>{
    assert.match(String(input),/\/drafts\/one\/finmod$/);
    if(options?.method==='PUT'){
      const body=JSON.parse(String(options.body));assert.equal(body.expected_revision,state.revision);
      state={...state,revision:state.revision+1,state:body.state};
    }
    return response(state);
  };
  const app=render(<Editor/>);
  await waitFor(()=>assert.equal((screen.getByLabelText('工作草稿') as HTMLInputElement).disabled,false));
  fireEvent.change(screen.getByLabelText('工作草稿'),{target:{value:'季度收入分析'}});
  await waitFor(()=>assert.equal(state.state.goal,'季度收入分析'),{timeout:2000});
  app.unmount();window.localStorage.clear();render(<Editor/>);
  await waitFor(()=>assert.equal((screen.getByLabelText('工作草稿') as HTMLInputElement).value,'季度收入分析'));
});
test('a server revision conflict preserves edited input and stops automatic retries',async()=>{
  let writes=0;
  globalThis.fetch=async (_input,options)=>options?.method==='PUT' ? (writes++,response(null,409)) : response({revision:2,state:{goal:'原目标'},data_revision:'data',saved_data_revision:'data'});
  render(<Editor/>);
  await waitFor(()=>assert.equal((screen.getByLabelText('工作草稿') as HTMLInputElement).disabled,false));
  fireEvent.change(screen.getByLabelText('工作草稿'),{target:{value:'不可丢失的目标'}});
  await screen.findByRole('alert',{}, {timeout:2000});
  assert.equal((screen.getByLabelText('工作草稿') as HTMLInputElement).value,'不可丢失的目标');
  assert.equal(writes,1);
});

test('reopening a conflicting local backup does not adopt the newer server revision',async()=>{
  window.localStorage.setItem('finance.workspace.v1:one:finmod',JSON.stringify({revision:2,dirty:true,value:{goal:'本地修改'}}));
  globalThis.fetch=async()=>response({revision:3,state:{goal:'其他页面修改'},data_revision:'data',saved_data_revision:'data'});
  const app=render(<Editor/>);
  await screen.findByRole('alert');app.unmount();render(<Editor/>);
  await screen.findByRole('alert');
  assert.equal((screen.getByLabelText('工作草稿') as HTMLInputElement).value,'本地修改');
});

test('upstream changes invalidate computed results while preserving business inputs',()=>{
  const original={goal:'收入分析',selectedFiles:[],workflow:{step:4,reached:5},runResult:{run_id:'old'},reportResult:{rel_path:'old.md'},mappingOverrides:{income:'收入'}};
  const reset=invalidateFinmod(original);
  assert.equal(reset.goal,'收入分析');assert.deepEqual(reset.selectedFiles,[]);
  assert.equal(reset.runResult,undefined);assert.equal(reset.reportResult,undefined);
  const chartChanged=invalidateFinmod(original,4);
  assert.deepEqual(chartChanged.mappingOverrides,{income:'收入'});
  assert.deepEqual(chartChanged.workflow,{step:4,reached:4});
  const recon=invalidateRecon({config:{tolerance:0.01},workflow:{step:3,reached:6},writebackDone:true,reconData:{summary:{ok:1}}},true);
  assert.deepEqual(recon.config,{tolerance:0.01});assert.equal(recon.writebackDone,undefined);assert.deepEqual(recon.workflow,{step:3,reached:4});
});

test('a local draft from an old data revision loses derived results even without a saved server draft',async()=>{
  window.localStorage.setItem('finance.workspace.v1:one:finmod',JSON.stringify({revision:0,dataRevision:'old-data',dirty:true,value:{goal:'保留目标',runResult:{run_id:'stale'}}}));
  globalThis.fetch=async()=>response({revision:0,state:{},data_revision:'new-data',saved_data_revision:''});
  const hook=renderHook(()=>useWorkspaceDraft<Record<string,unknown>>('one','finmod',{},invalidateFinmod));
  await waitFor(()=>assert.equal(hook.result.current.ready,true));
  assert.equal(hook.result.current.value.goal,'保留目标');
  assert.equal(hook.result.current.value.runResult,undefined);
  assert.equal(hook.result.current.dataChanged,true);
});

test('restoring a completed modeling task reopens its existing report without generating it again',()=>{
  const restored=restoreFinmodTask({goal:'分析'},{run_id:'saved',params:{file_names:['data.xlsx'],model_ids:['d1'],mappings:{}},result:{run_id:'saved'},reports:[{rel_path:'报告/报告.md',filename:'报告.md',used_llm:false}]} as any,[{file_name:'data.xlsx'}] as any);
  assert.equal((restored.reportResult as any).rel_path,'报告/报告.md');
  assert.equal((restored.reportResult as any).used_llm,false);
  assert.deepEqual((restored.selectedFiles as any[]).map(file=>file.file_name),['data.xlsx']);
});
