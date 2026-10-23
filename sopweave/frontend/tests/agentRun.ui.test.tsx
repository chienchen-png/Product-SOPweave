import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {act,cleanup,renderHook,waitFor} from '@testing-library/react';
import {useAgentRun} from '../src/hooks/useAgentRun';
afterEach(cleanup);

test('pending text is delivered before a terminal error and cannot erase the failure',async()=>{
  const seen:string[]=[];
  const events=[{seq:1,type:'message.delta',payload:{delta:'开始检查'}},{seq:2,type:'run.failed',payload:{message:'智能体运行失败'}}];
  globalThis.fetch=async()=>new Response(new ReadableStream({start(controller){
    controller.enqueue(new TextEncoder().encode(events.map(event=>`data: ${JSON.stringify({run_id:'failed',...event})}\n\n`).join('')));
    controller.close();
  }}));
  const hook=renderHook(()=>useAgentRun());
  await act(async()=>{await hook.result.current.start({run_id:'failed',prompt:'test',project_id:'p',conversation_id:'p',agent_id:'general-agent',model_id:'m',work_dir:'.'},{onText:delta=>seen.push('text:'+delta),onError:message=>seen.push('error:'+message)});});
  assert.deepEqual(seen,['text:开始检查','error:智能体运行失败']);
  assert.equal(hook.result.current.status,'failed');
});
test('leaving a page only detaches; reconnecting replays after cursor without another POST',async()=>{
  const calls:{url:string;method?:string;body?:string}[]=[];
  let stream:ReadableStreamDefaultController<Uint8Array>;
  const frame=(seq:number,type:string,payload={})=>new TextEncoder().encode(`data: ${JSON.stringify({run_id:'run-test',seq,type,payload})}\n\n`);
  globalThis.fetch=async(url,options)=>{
    calls.push({url:String(url),method:options?.method,body:options?.body as string});
    return new Response(new ReadableStream({start(controller){stream=controller;controller.enqueue(frame(1,'run.created'));},cancel(){}}));
  };
  const first=renderHook(()=>useAgentRun());
  let pending:Promise<void>;
  act(()=>{pending=first.result.current.start({run_id:'run-test',prompt:'test',project_id:'p',conversation_id:'p',agent_id:'general-agent',model_id:'m',work_dir:'.'},{});});
  await waitFor(()=>assert.equal(first.result.current.runId,'run-test'));
  assert.equal(JSON.parse(calls[0].body!).background,true);
  first.unmount();stream!.close();await pending!;
  assert.equal(calls.filter(c=>c.url.endsWith('/cancel')).length,0);
  const seen:string[]=[];
  globalThis.fetch=async(url,options)=>{
    calls.push({url:String(url),method:options?.method});
    return new Response(new ReadableStream({start(c){c.enqueue(frame(1,'message.delta',{delta:'旧'}));c.enqueue(frame(2,'message.delta',{delta:'新'}));c.enqueue(frame(3,'run.completed'));c.close();}}));
  };
  const second=renderHook(()=>useAgentRun());
  await act(async()=>{await second.result.current.attach('run-test',1,{onText:text=>seen.push(text)});});
  assert.deepEqual(seen,['新']);assert.equal(second.result.current.status,'completed');
  assert.equal(calls.filter(c=>c.method==='POST').length,1);
});

test('synchronous duplicate starts produce exactly one run request',async()=>{
  const calls:{url:string;method?:string}[]=[];
  const frame=(seq:number,type:string,payload={})=>new TextEncoder().encode(`data: ${JSON.stringify({run_id:'run-once',seq,type,payload})}\n\n`);
  globalThis.fetch=async(url,options)=>{
    const href=String(url);
    calls.push({url:href,method:options?.method});
    const events=options?.method==='POST'
      ?[frame(1,'run.created')]
      :[frame(2,'run.completed',{output:'ok'})];
    return new Response(new ReadableStream({start(controller){
      for(const event of events)controller.enqueue(event);
      controller.close();
    }}));
  };
  const hook=renderHook(()=>useAgentRun());
  const request={run_id:'run-once',prompt:'只发送一次',project_id:'p',conversation_id:'p',agent_id:'general-agent',model_id:'m',work_dir:'.'};
  await act(async()=>{
    await Promise.all([hook.result.current.start(request,{}),hook.result.current.start(request,{})]);
  });
  assert.equal(calls.filter(call=>call.method==='POST').length,1);
});
