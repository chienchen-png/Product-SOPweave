import React from 'react';
import {test,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import ChatPage from '../src/pages/ChatPage';
import {FeedbackProvider} from '../src/components/ui/Feedback';
import type {Project,ModelOption} from '../src/lib/api';

const originalFetch=globalThis.fetch;
afterEach(()=>{cleanup();window.localStorage.clear();globalThis.fetch=originalFetch;});

test('composer preserves IME, sends once, streams reasoning and respects the reader scroll position',async()=>{
  const requests:string[]=[];
  const scrollCalls:unknown[]=[];
  let pending:ReadableStreamDefaultController<Uint8Array>|undefined;
  HTMLElement.prototype.scrollTo=function(options){scrollCalls.push(options);};
  const frame=(seq:number,type:string,payload={})=>new TextEncoder().encode(`data: ${JSON.stringify({run_id:'composer-run',seq,type,payload})}\n\n`);
  globalThis.fetch=async(url,options)=>{
    const path=String(url);
    if(path==='/api/agent/runs'&&options?.method==='POST'){
      requests.push(String(options.body));
      return new Response(new ReadableStream({start(c){pending=c;c.enqueue(frame(1,'run.created'));}}));
    }
    const data=path.includes('/history')?{runs:[]}:path.includes('/interactions')?{interactions:[]}:path.includes('/skills')?{skills:[]}:{};
    return new Response(JSON.stringify({success:true,data}),{headers:{'content-type':'application/json'}});
  };
  render(<FeedbackProvider><ChatPage project={{id:'composer-project',name:'Test'} as Project}
    models={[{id:'test-model',name:'Test Model',type:'chat',provider_id:'test',status:'verified'} as ModelOption]}
    agentId="general-agent" providers={[{id:'test',name:'Test'}]} defaultModel="test-model"/></FeedbackProvider>);
  assert.equal(screen.queryByText('General'),null);
  assert.ok(screen.getByLabelText('选择本次任务的 SOP').closest('.composer-shadow'));
  const input=screen.getByPlaceholderText(/输入任务/) as HTMLTextAreaElement;
  await waitFor(()=>assert.equal(!!screen.queryByText('恢复项目记录…'),false));
  fireEvent.click(screen.getByLabelText('选择本次任务的 SOP'));
  fireEvent.click(await screen.findByRole('button',{name:'仅内置流程'}));
  fireEvent.change(input,{target:{value:'核对金额'}});
  await waitFor(()=>assert.equal((screen.getByTitle('发送') as HTMLButtonElement).disabled,false));
  fireEvent.keyDown(input,{key:'Enter',isComposing:true,keyCode:229});
  fireEvent.keyDown(input,{key:'Enter',shiftKey:true});
  assert.equal(requests.length,0);
  assert.equal(input.value,'核对金额');
  await act(async()=>{
    const button=screen.getByTitle('发送');
    button.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));
    button.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));
  });
  assert.equal(requests.length,1);
  assert.equal(JSON.parse(requests[0]).agent_id,'general-agent');
  assert.equal(JSON.parse(requests[0]).sop_mode,'none');
  assert.equal(screen.getAllByText('核对金额').length,1);
  assert.ok(screen.getByTitle('停止'));
  const scroller=document.querySelector('.app-page .overflow-y-auto') as HTMLElement;
  Object.defineProperties(scroller,{scrollHeight:{value:2000,configurable:true},clientHeight:{value:500,configurable:true}});
  fireEvent.scroll(scroller);
  const callsBefore=scrollCalls.length;
  await act(async()=>{
    pending!.enqueue(frame(2,'context.updated',{omitted_items:4}));
    pending!.enqueue(frame(3,'reasoning.delta',{delta:'核对来源与金额。'}));
    pending!.enqueue(frame(4,'message.delta',{delta:'TOTAL_OK'}));
    await new Promise(resolve=>setTimeout(resolve,20));
  });
  assert.ok(screen.getByText('TOTAL_OK'));
  assert.ok(screen.getByRole('note').textContent?.includes('省略 4 条'));
  assert.ok(screen.getByText('核对来源与金额。'));
  assert.equal(scrollCalls.length,callsBefore);
  fireEvent.click(screen.getByRole('button',{name:'回到底部'}));
  assert.equal(scrollCalls.length,callsBefore+1);
  await act(async()=>{pending!.enqueue(frame(5,'tool.started',{tool_id:'timed',name:'read_text',arguments:{path:'notes.txt'}}));pending!.enqueue(frame(6,'tool.completed',{tool_id:'timed',output:{success:true},duration_ms:275}));pending!.enqueue(frame(7,'run.completed',{output:'TOTAL_OK'}));pending!.close();});
  await waitFor(()=>assert.ok(screen.getByTitle('发送')));
  assert.ok(screen.getAllByText('275ms').length>0);
});


test('restored conversation retains context omission notice',async()=>{
  globalThis.fetch=async url=>{
    const data=String(url).includes('/history')?{runs:[{run_id:'restored',status:'completed',user_prompt:'当前任务',assistant_text:'已完成',tool_cards:[],context_omitted_items:6}]}:String(url).includes('/interactions')?{interactions:[]}:{skills:[]};
    return new Response(JSON.stringify({success:true,data}),{headers:{'content-type':'application/json'}});
  };
  render(<FeedbackProvider><ChatPage project={{id:'restore-project',name:'Test'} as Project}
    models={[{id:'test-model',name:'Test Model',type:'chat',provider_id:'test',status:'verified'} as ModelOption]}
    agentId="general-agent" providers={[{id:'test',name:'Test'}]} defaultModel="test-model"/></FeedbackProvider>);
  await waitFor(()=>assert.equal(!!screen.queryByText('已完成'),true));
  assert.ok(screen.getByRole('note').textContent?.includes('省略 6 条'));
});
