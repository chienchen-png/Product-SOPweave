import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import DashboardPage from '../src/pages/DashboardPage';
import TokenTrendChart from '../src/components/dashboard/TokenTrendChart';
const original=globalThis.fetch;
afterEach(()=>{cleanup();globalThis.fetch=original;Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});});
const json=(data:unknown)=>new Response(JSON.stringify({success:true,data}));
function summary(days=7,tokens:number|null=120){return {ts:'2026-10-18T02:00:00Z',timezone:'Asia/Shanghai',range:{days,start:'2026-10-12',end:'2026-10-18'},today:{tokens,ai_requests:3,runs:5,success:3,failed:1,duration_sec:3600},token_trend:Array.from({length:days},(_,i)=>({day:`2026-10-${String(i+1).padStart(2,'0')}`,tokens,missing_requests:0})),token_ranking:{providers:[{id:'p',name:'测试供应商',tokens:120}],models:[{provider_id:'p',model_id:'m',provider_name:'测试供应商',name:'很长的模型名称'.repeat(10),tokens:120}]},quality:{tokens_available:true,runtime_available:true,collector_warning:false,tracking_started_at:'2026-10-18T00:00:00Z',today_missing:0,range_missing:0}};}

test('statistics home has four metrics two charts and a live local clock',async()=>{
 const calls:string[]=[];
 globalThis.fetch=async url=>{calls.push(String(url));return json(summary());};
 render(<DashboardPage/>);
 await screen.findByText('120');
 assert.equal(screen.getAllByRole('article').length,4);
 assert.ok(screen.getByRole('heading',{name:'Token 消耗趋势'}));
 assert.ok(screen.getByRole('heading',{name:'模型用量'}));
 assert.ok(screen.getByText('成功 3 · 失败 1'));
 assert.equal(calls.length,1);assert.ok(calls[0].startsWith('/api/dashboard/summary?days=7&timezone='));
 assert.equal(screen.queryByRole('button',{name:'模型配置'}),null);
 assert.ok(screen.getByText('本机时间'));
 assert.match(screen.getByText('本机时间').parentElement?.textContent || '',/\d{2}:\d{2}:\d{2}/);
 assert.equal(screen.queryByRole('button',{name:'任务与报告'}),null);
 fireEvent.click(screen.getByRole('button',{name:'模型',exact:true}));assert.equal(calls.length,1);
 assert.equal(screen.queryByText('开始任务'),null);
});

test('local clock follows computer time and advances every second',async()=>{
 globalThis.fetch=async()=>json(summary());
 const view=render(<DashboardPage/>);
 const clock=document.querySelector('time');
 assert.ok(clock);
 const initial=clock.textContent;
 await waitFor(()=>assert.notEqual(clock.textContent,initial),{timeout:2500});
 view.unmount();
});

test('range races keep newest result and failed refresh retains its timestamp',async()=>{
 let resolveOld!:(value:Response)=>void;let fail=false;
 globalThis.fetch=async url=>{if(fail)throw new Error('连接失败');return String(url).includes('days=30')?json(summary(30,300)):new Promise(resolve=>{resolveOld=resolve;});};
 render(<DashboardPage/>);
 await waitFor(()=>assert.ok(resolveOld));
 fireEvent.click(screen.getByRole('button',{name:'30 日'}));await screen.findByText('300');
 await act(async()=>{resolveOld(json(summary(7,7)));});
 assert.equal(screen.queryByText('7'),null);
 fail=true;fireEvent.click(screen.getByRole('button',{name:'刷新统计'}));
 await screen.findByRole('alert');assert.ok(screen.getByText('300'));assert.ok(screen.getByText(/上次更新/));
});

test('zero missing and unavailable values remain distinct',async()=>{
 globalThis.fetch=async()=>json({...summary(7,null),quality:{...summary().quality,today_missing:2,range_missing:2}});
 const view=render(<DashboardPage/>);
 await screen.findByText(/今日 2 次请求未返回用量/);assert.ok(screen.getByText('—'));
 view.unmount();globalThis.fetch=async()=>json(summary(7,0));
 render(<DashboardPage/>);await screen.findByText('0');
});

test('hidden page pauses polling and visible page reloads with abort cleanup',async()=>{
 const intervals:Function[]=[];const cleared:number[]=[];const signals:AbortSignal[]=[];
 const set=window.setInterval,clear=window.clearInterval;
 window.setInterval=((callback:Function)=>{intervals.push(callback);return intervals.length;}) as typeof window.setInterval;
 window.clearInterval=((id:number)=>{cleared.push(id);}) as typeof window.clearInterval;
 let calls=0;
 globalThis.fetch=async(_url,options)=>{calls++;if(options?.signal)signals.push(options.signal);return json(summary());};
 try {
  const view=render(<DashboardPage/>);await screen.findByText('120');
  Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true});
  await act(async()=>{document.dispatchEvent(new Event('visibilitychange'));for(const callback of intervals)callback();});
  assert.equal(calls,1);assert.ok(cleared.length);
  Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});
  await act(async()=>{document.dispatchEvent(new Event('visibilitychange'));});assert.equal(calls,2);
  view.unmount();assert.ok(signals.every(signal=>signal.aborted));
 } finally {window.setInterval=set;window.clearInterval=clear;}
});

test('one known day in thirty remains visible as a point and missing days stay gaps',async()=>{
 const points=Array.from({length:30},(_,i)=>({day:`2026-10-${i+1}`,tokens:i===29?7:null,missing_requests:0}));
 render(<TokenTrendChart points={points}/>);
 const label='每日 Token 总量，空白日期表示未采集或用量缺失';
 await waitFor(()=>assert.ok((globalThis as any).__dashboardChartOptions?.[label]));
 const series=(globalThis as any).__dashboardChartOptions[label].series[0];
 assert.equal(series.showSymbol,true);assert.equal(series.connectNulls,false);
 assert.equal(series.data.filter((value:unknown)=>value===null).length,29);
});
