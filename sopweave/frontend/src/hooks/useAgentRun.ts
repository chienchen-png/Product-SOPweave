import { useCallback, useEffect, useRef, useState } from 'react';
import {cancelAgentRun,resumeAgentRun,streamAgentRun,watchAgentRun,type AgentResumeCommand,type AgentRunRequest,type AgentStreamHandlers} from '../lib/agentStream';

type RunStatus='idle'|'running'|'waiting'|'completed'|'failed'|'stopped'|'disconnected';

/** Execution belongs to the server. A mounted page only owns its subscription. */
export function useAgentRun(){
  const [runId,setRunId]=useState<string|null>(null);
  const [status,setStatus]=useState<RunStatus>('idle');
  const [error,setError]=useState<string|null>(null);
  const controllerRef=useRef<AbortController|null>(null);
  const startPending=useRef(false);
  const handlersRef=useRef<AgentStreamHandlers>({});
  const sequence=useRef(0), identifier=useRef<string|null>(null);
  const text=useRef(''),frame=useRef<number|null>(null);
  const flush=useCallback(()=>{
    if(frame.current!==null)cancelAnimationFrame(frame.current);
    frame.current=null;
    const delta=text.current;text.current='';
    if(delta)handlersRef.current.onText?.(delta);
  },[]);
  const transport=useCallback(async(id:string,after:number,handlers:AgentStreamHandlers,send:(wrapped:AgentStreamHandlers,signal:AbortSignal)=>Promise<void>)=>{
    flush();controllerRef.current?.abort();
    const controller=new AbortController();controllerRef.current=controller;
    identifier.current=id;sequence.current=after;handlersRef.current=handlers;
    setRunId(id);setError(null);setStatus('running');
    let settled=false;
    const wrapped:AgentStreamHandlers={...handlers,
      onEvent:event=>{sequence.current=Math.max(sequence.current,event.seq);handlers.onEvent?.(event);},
      onText:delta=>{text.current+=delta;if(frame.current===null)frame.current=requestAnimationFrame(flush);},
      onWaiting:reason=>{settled=true;flush();setStatus('waiting');handlers.onWaiting?.(reason);},
      onDone:result=>{settled=true;flush();setStatus(result==='cancelled'?'stopped':result);handlers.onDone?.(result);},
      onError:message=>{flush();setError(message);handlers.onError?.(message);},
    };
    try{
      try{await send(wrapped,controller.signal);}
      catch(cause){
        if(controller.signal.aborted||(cause as Error&{status?:number}).status)throw cause;
        await watchAgentRun(id,sequence.current,wrapped,controller.signal);
        settled=true;
      }
      if(!settled)await watchAgentRun(id,sequence.current,wrapped,controller.signal);
    }catch(cause){
      if(controller.signal.aborted||(cause as Error).name==='AbortError')return;
      flush();const message=(cause as Error).message||'连接中断，运行状态由服务端保存';
      setError(message);setStatus((cause as Error&{status?:number}).status?'failed':'disconnected');
      handlers.onError?.(message);throw cause;
    }finally{if(controllerRef.current===controller)controllerRef.current=null;}
  },[flush]);
  const start=useCallback((request:AgentRunRequest,handlers:AgentStreamHandlers)=>{
    if(startPending.current)return Promise.resolve();
    startPending.current=true;
    const id=request.run_id||`run-${crypto.randomUUID()}`;
    return transport(id,0,handlers,(wrapped,signal)=>streamAgentRun({...request,run_id:id,background:true},wrapped,signal))
      .finally(()=>{startPending.current=false;});
  },[transport]);
  const attach=useCallback((id:string,after:number,handlers:AgentStreamHandlers)=>transport(id,after,handlers,(wrapped,signal)=>watchAgentRun(id,after,wrapped,signal)),[transport]);
  const resume=useCallback((id:string,command:AgentResumeCommand,handlers:AgentStreamHandlers={})=>transport(id,identifier.current===id?sequence.current:0,handlers,
    (wrapped,signal)=>resumeAgentRun(id,command,wrapped,signal,{background:true,afterSeq:sequence.current})),[transport]);
  const cancel=useCallback(async(target?:string)=>{
    const id=target||identifier.current;if(id)await cancelAgentRun(id);
    flush();controllerRef.current?.abort();setStatus('stopped');
  },[flush]);
  useEffect(()=>()=>{controllerRef.current?.abort();if(frame.current!==null)cancelAnimationFrame(frame.current);text.current='';},[]);
  return {runId,status,error,running:status==='running',start,attach,resume,cancel};
}
