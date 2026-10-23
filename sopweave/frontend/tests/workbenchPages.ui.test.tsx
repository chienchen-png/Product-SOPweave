import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {MemoryRouter} from 'react-router-dom';
import ApplicationTaskStatus from '../src/components/ApplicationTaskStatus';
import DataCheckAppPage from '../src/pages/DataCheckAppPage';
import FinModAppPage from '../src/pages/FinModAppPage';
import PageBoundary from '../src/components/PageBoundary';
import {FeedbackProvider} from '../src/components/ui/Feedback';

const original=globalThis.fetch;
afterEach(()=>{cleanup();window.localStorage.clear();globalThis.fetch=original;});
const json=(data:unknown)=>new Response(JSON.stringify({success:true,data}));
test('failed page imports keep a retry action instead of blanking the workbench',()=>{
  function Broken():never{throw new Error('Failed to fetch dynamically imported module');}
  const previous=console.error;console.error=()=>{};let retries=0;
  try{render(<PageBoundary onRetry={()=>retries++}><Broken/></PageBoundary>);}finally{console.error=previous;}
  fireEvent.click(screen.getByRole('button',{name:'重新加载页面'}));assert.equal(retries,1);
});
test('restoring an application result requires confirmation and rejects changed data',async()=>{
  let restored=false;
  const task={run_id:'one',project_id:'p',kind:'recon',title:'核对',status:'completed'};
  globalThis.fetch=async url=>String(url).includes('/tasks/one')?json({...task,result:{report:'旧结果'},data_revision:'old'}):json({tasks:[task]});
  render(<MemoryRouter><FeedbackProvider><ApplicationTaskStatus projectId="p" currentRunId="" localBusy={false} dataRevision="new" onActive={()=>{}} onRestore={()=>{restored=true;}}/></FeedbackProvider></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button',{name:'恢复任务结果'}));
  assert.equal(restored,false);
  fireEvent.click(screen.getByRole('button',{name:'确认',exact:true}));
  assert.ok(await screen.findByText(/导入数据已变化/));assert.equal(restored,false);
});

test('application task recovery stays inside the current app without a global task link',async()=>{
  const task={run_id:'running-one',project_id:'p',kind:'recon',title:'核对',status:'running'};
  globalThis.fetch=async url=>String(url).includes('/tasks/')?json(task):json({tasks:[task]});
  render(<MemoryRouter><FeedbackProvider><ApplicationTaskStatus projectId="p" currentRunId="" localBusy={false} dataRevision="data" onActive={()=>{}} onRestore={()=>{}}/></FeedbackProvider></MemoryRouter>);
  assert.ok(await screen.findByText(/当前应用工作区/));
  assert.equal(screen.queryByRole('button',{name:'任务与报告'}),null);
});

test('reconciliation navigation uses the workspace initialized after the hidden project loads',async()=>{
  globalThis.fetch=async(url,options)=>{
    const path=String(url);
    if(path.startsWith('/api/projects'))return json([{id:'recon',name:'__app_recon__',work_dir:'.'}]);
    if(path.includes('/drafts/'))return json({state:options?.body?JSON.parse(String(options.body)).state:{},revision:0,data_revision:'data',saved_data_revision:'data'});
    if(path.includes('/tasks?'))return json({tasks:[],next_cursor:''});
    if(path.includes('/history/recon'))return json({records:[]});
    return json({files:[],entries:[]});
  };
  render(<MemoryRouter><FeedbackProvider><DataCheckAppPage models={[]} providers={[]} defaultModel=""/></FeedbackProvider></MemoryRouter>);
  const next=await screen.findByRole('button',{name:/2\s*匹配键/});
  fireEvent.click(next);
  assert.ok(await screen.findByRole('heading',{name:'匹配键'}));
});

test('restored verified parameters stay complete and B differences are not offered for automatic writeback',async()=>{
  const state={workflow:{step:6,reached:6},params:{baseFile:'base.xlsx',detailFile:'detail.xlsx',matchKeys:{k1:'id'},variables:['金额'],baseAmountCol:'金额',tolerance:1},runId:'saved',reconData:{summary:{ok:1,a1:0,a2:0,b:1,c:0},diffs:[{level:'B',contract:'A001',base_amount:100,target_amount:90,diff:10}]}};
  globalThis.fetch=async url=>{
    const path=String(url);
    if(path.startsWith('/api/projects'))return json([{id:'recon',name:'__app_recon__',work_dir:'.'}]);
    if(path.includes('/drafts/'))return json({state,revision:1,data_revision:'data',saved_data_revision:'data'});
    if(path.includes('/tasks?'))return json({tasks:[],next_cursor:''});
    return json({files:[],records:[],entries:[]});
  };
  render(<MemoryRouter><FeedbackProvider><DataCheckAppPage models={[]} providers={[]} defaultModel=""/></FeedbackProvider></MemoryRouter>);
  assert.ok(await screen.findByRole('button',{name:/✓\s*数据文件/}));
  const boxes=await screen.findAllByRole('checkbox');assert.equal((boxes.at(-1) as HTMLInputElement).disabled,true);
  assert.ok(screen.getByText(/B\/C 请先在数据区人工复核/));
});

test('restored completed reconciliation reloads output files',async()=>{
  const state={workflow:{step:6,reached:6},params:{baseFile:'base.xlsx',detailFile:'detail.xlsx',matchKeys:{k1:'id'},variables:['金额'],baseAmountCol:'金额',tolerance:1},runId:'saved',reconData:{summary:{ok:1,a1:0,a2:0,b:0,c:0},diffs:[]},writebackDone:true,writebackReport:'## 修改报告'};
  globalThis.fetch=async url=>{
    const path=String(url);
    if(path.startsWith('/api/projects'))return json([{id:'recon',name:'__app_recon__',work_dir:'.'}]);
    if(path.includes('/drafts/'))return json({state,revision:1,data_revision:'data',saved_data_revision:'data'});
    if(path.includes('/tasks?'))return json({tasks:[],next_cursor:''});
    if(path.includes('/files/tree?'))return json({path:'',entries:[{name:'修改后.xlsx',path:'修改后.xlsx',size:2048,type:'file'}]});
    return json({files:[],records:[],entries:[]});
  };
  render(<MemoryRouter><FeedbackProvider><DataCheckAppPage models={[]} providers={[]} defaultModel=""/></FeedbackProvider></MemoryRouter>);
  assert.ok(await screen.findByText('修改后.xlsx'));
  assert.equal(screen.queryByText('（暂无输出文件）'),null);
});

test('reconciliation writeback requires a cancellable confirmation',async()=>{
  let writebackCalls=0;
  const state={
    workflow:{step:6,reached:6},
    params:{baseFile:'base.xlsx',detailFile:'detail.xlsx',matchKeys:{k1:'id'},variables:['金额'],baseAmountCol:'金额',tolerance:1},
    runId:'run-confirm',
    reconData:{summary:{ok:0,a1:1,a2:1,b:0,c:0},diffs:[
      {level:'A1',contract:'A001',base_amount:100,target_amount:null,diff:null,reason:'空值'},
      {level:'A2',contract:'A002',base_amount:200,target_amount:201,diff:-1,reason:'小额误差'},
    ]},
  };
  globalThis.fetch=async(url)=>{
    const path=String(url);
    if(path.startsWith('/api/projects'))return json([{id:'recon',name:'__app_recon__',work_dir:'.'}]);
    if(path.includes('/drafts/'))return json({state,revision:1,data_revision:'data',saved_data_revision:'data'});
    if(path.includes('/tasks?'))return json({tasks:[],next_cursor:''});
    if(path.endsWith('/api/apps/recon/writeback')){writebackCalls++;return json({report:'已写回'});}
    return json({files:[],records:[],entries:[]});
  };
  render(<MemoryRouter><FeedbackProvider><DataCheckAppPage models={[]} providers={[]} defaultModel=""/></FeedbackProvider></MemoryRouter>);
  const boxes=await screen.findAllByRole('checkbox');
  const rowBoxes=boxes.slice(-2);
  fireEvent.click(rowBoxes[0]);fireEvent.click(rowBoxes[1]);
  fireEvent.click(await screen.findByRole('button',{name:/确认修改（2 条）/}));
  assert.ok(await screen.findByText(/将向「detail.xlsx」写回 2 条/));
  fireEvent.click(screen.getByRole('button',{name:'取消',exact:true}));
  assert.equal(writebackCalls,0);
});

test('rerunning financial models clears the previous report result',async()=>{
  let runBody:Record<string,unknown>|null=null;
  const state={
    goal:'验证新执行不混用旧报告',
    macroPhase:'varmap',
    reportPhase:'report',
    workflow:{step:3,reached:3},
    periodCol:'日期',
    selectedFiles:[{file_name:'数据.xlsx',id:'f1',sheets:[{sheet_name:'数据'}]}],
    selectedModels:['g1-descriptive-stats'],
    chartManifest:[{id:'c1',template:'histogram',variant:'histogram',name:'直方图',level:'L1',color_scheme:'auto',importance:'secondary',data_source:[],reason:'',variants:['histogram']}],
    varmap:{all_cols:['金额','日期'],period_cols:['日期'],models:[],message:''},
    reportResult:{rel_path:'旧报告.md',filename:'旧报告.md',project_id:'finmod',report:'OLD_REPORT_MUST_DISAPPEAR'},
    savedCharts:[{filename:'旧图.svg',rel_path:'旧图.svg',format:'svg'}],
  };
  globalThis.fetch=async(url,options)=>{
    const path=String(url);
    if(path.startsWith('/api/projects'))return json([{id:'finmod',name:'__app_finmod__',work_dir:'.'}]);
    if(path.includes('/drafts/'))return json({state,revision:2,data_revision:'data',saved_data_revision:'data'});
    if(path.includes('/tasks?'))return json({tasks:[],next_cursor:''});
    if(path.includes('/reports?'))return json({reports:[]});
    if(path.endsWith('/api/apps/finmod/run')){
      runBody=JSON.parse(String(options?.body||'{}'));
      return json({run_id:'new-run',runId:'new-run',models:[{model_id:'g1-descriptive-stats',code:'G1',name:'描述性统计',ok:true,summary:{}}],charts:[],row_count:1,data_stats:{rows:1,columns:2,period_col:'日期'}});
    }
    if(path.endsWith('/api/apps/finmod/meta'))return json({model_categories:[],models:[],charts:[],chart_levels:[]});
    return json({files:[{file_name:'数据.xlsx',id:'f1',status:'done',sheets:[{sheet_name:'数据'}]}],entries:[],records:[]});
  };
  render(<MemoryRouter><FeedbackProvider><FinModAppPage models={[]} providers={[]} defaultModel=""/></FeedbackProvider></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button',{name:/下一步（AI 撰写报告）/}));
  await waitFor(()=>assert.equal(screen.queryByText(/OLD_REPORT_MUST_DISAPPEAR/),null));
  assert.ok(await screen.findByRole('button',{name:'生成分析报告'},{timeout:3000}));
  assert.equal(runBody?.period_col,'日期');
});
