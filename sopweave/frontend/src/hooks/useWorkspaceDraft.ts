import {useCallback,useEffect,useMemo,useRef,useState,type Dispatch,type SetStateAction} from 'react';
import {fetchWorkspaceDraft,saveWorkspaceDraft,type WorkspaceKind} from '../lib/api/workbench';
import {readMigratedStorage} from '../lib/brand';

interface State<T> {scope:string;value:T;revision:number;dataRevision:string;ready:boolean;dirty:boolean;saving:boolean;error:string;dataChanged:boolean}
export interface WorkspaceDraft<T> extends State<T> {setValue:Dispatch<SetStateAction<T>>;saveNow:()=>Promise<void>;reload:()=>void;checkData:()=>Promise<void>}

/** Server CAS with an immediate browser backup for in-flight edits. */
export function useWorkspaceDraft<T extends Record<string,unknown>>(projectId:string,kind:WorkspaceKind,initial:T, invalidateData?:(value:T)=>T):WorkspaceDraft<T> {
  const scope=`${projectId}:${kind}`, key=`sopweave.workspace.v1:${scope}`;
  const initialRef=useRef(initial), invalidateRef=useRef(invalidateData);
  invalidateRef.current=invalidateData;
  const empty=():State<T>=>({scope,value:initialRef.current,revision:0,dataRevision:'',ready:false,dirty:false,saving:false,error:'',dataChanged:false});
  const [state,setState]=useState<State<T>>(empty);
  const current=useRef(state), mounted=useRef(true), pending=useRef<Promise<void>|null>(null);
  const [refresh,setRefresh]=useState(0);
  const acceptRemote=useRef(false);
  if(current.current.scope!==scope) current.current=empty();
  const publish=useCallback((value:State<T>)=>{
    if(value.scope!==current.current.scope || !mounted.current)return;
    current.current=value;setState(value);
  },[]);
  const backup=useCallback((value:State<T>)=>{
    try {window.localStorage.setItem(key,JSON.stringify({value:value.value,revision:value.revision,dataRevision:value.dataRevision,dirty:value.dirty}));return '';}
    catch {return '浏览器备份失败，请等待服务端保存完成后离开页面。';}
  },[key]);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    if(!projectId)return;
    let disposed=false;
    const controller=new AbortController();
    publish({...current.current,ready:false,error:''});
    void fetchWorkspaceDraft<T>(projectId,kind,controller.signal).then(document=>{
      if(disposed)return;
      let value={...initialRef.current,...document.state},dirty=false,error='',revision=document.revision,sourceDataRevision=document.saved_data_revision;
      try {
        const saved=JSON.parse(readMigratedStorage(key)||'null');
        if(!acceptRemote.current&&saved?.dirty&&saved.value&&typeof saved.value==='object'&&!Array.isArray(saved.value)){
          value={...initialRef.current,...saved.value};dirty=true;
          if(typeof saved.dataRevision==='string')sourceDataRevision=saved.dataRevision;
          if(saved.revision!==document.revision){revision=saved.revision;error='服务端草稿已更新，本地未保存输入已保留；请比较后重新加载服务端版本。';}
        }
      }catch{}
      acceptRemote.current=false;
      const dataChanged=!!sourceDataRevision&&sourceDataRevision!==document.data_revision;
      if(dataChanged&&invalidateRef.current){value=invalidateRef.current(value);dirty=true;}
      const next={scope,value,revision,dataRevision:document.data_revision,ready:true,dirty,saving:false,error,dataChanged};
      publish(next);backup(next);
    }).catch(cause=>{if(!disposed)publish({...current.current,ready:false,error:cause.message});});
    return()=>{disposed=true;controller.abort();};
  },[scope,projectId,kind,key,refresh,publish,backup]);
  const setValue=useCallback<Dispatch<SetStateAction<T>>>((action)=>{
    const previous=current.current;
    if(!mounted.current||previous.scope!==scope||!previous.ready)return;
    const value=typeof action==='function'?(action as (value:T)=>T)(previous.value):action;
    if(Object.is(value,previous.value))return;
    const next={...previous,value,dirty:true};
    const backupError=backup(next);
    publish({...next,error:previous.error||backupError});
  },[scope,backup,publish]);
  const saveNow=useCallback(async()=>{
    if(pending.current)return pending.current;
    const sent=current.current;
    if(!sent.ready||!sent.dirty||sent.scope!==scope)return;
    publish({...sent,saving:true,error:''});
    const operation=(async()=>{
      try{
        const result=await saveWorkspaceDraft(projectId,kind,sent.value,sent.revision,sent.dataRevision);
        if(current.current.scope!==scope)return;
        const next={...current.current,revision:result.revision,saving:false,error:'',dirty:current.current.value!==sent.value};
        publish(next);backup(next);
      }catch(cause){if(current.current.scope===scope)publish({...current.current,saving:false,error:(cause as Error).message});}
      finally{pending.current=null;}
    })();
    pending.current=operation;
    return operation;
  },[scope,projectId,kind,publish,backup]);
  useEffect(()=>{
    if(!state.ready||!state.dirty||state.error||state.saving)return;
    const timer=window.setTimeout(()=>{void saveNow();},500);
    return()=>window.clearTimeout(timer);
  },[state.value,state.ready,state.dirty,state.error,state.saving,saveNow]);
  const reload=useCallback(()=>{acceptRemote.current=true;setRefresh(value=>value+1);},[]);
  const checkData=useCallback(async()=>{
    if(pending.current)await pending.current;
    if(!projectId||!current.current.ready)return;
    try{
      const document=await fetchWorkspaceDraft<T>(projectId,kind);
      if(current.current.scope!==scope||document.data_revision===current.current.dataRevision)return;
      const value=invalidateRef.current?invalidateRef.current(current.current.value):current.current.value;
      const next={...current.current,value,dataRevision:document.data_revision,dirty:true,dataChanged:true,error:''};
      publish(next);backup(next);
    }catch(cause){if(current.current.scope===scope)publish({...current.current,error:(cause as Error).message});}
  },[projectId,kind,scope,publish,backup]);
  return {...current.current,setValue,saveNow,reload,checkData};
}

export function useWorkspaceField<T>(draft:WorkspaceDraft<Record<string,unknown>>,key:string,initial:T,invalidate?:(state:Record<string,unknown>)=>Record<string,unknown>):[T,Dispatch<SetStateAction<T>>] {
  const initialRef=useRef(initial);
  const invalidator=useRef(invalidate);invalidator.current=invalidate;
  const value=(key in draft.value?draft.value[key]:initialRef.current) as T;
  const setter=useCallback<Dispatch<SetStateAction<T>>>((action)=>draft.setValue(previous=>{
    const old=(key in previous?previous[key]:initialRef.current) as T;
    const next=typeof action==='function'?(action as (value:T)=>T)(old):action;
    if(Object.is(old,next)||JSON.stringify(old)===JSON.stringify(next))return previous;
    return {...(invalidator.current?invalidator.current(previous):previous),[key]:next};
  }),[draft.setValue,key]);
  return [value,setter];
}

export function useWorkspaceSet(draft:WorkspaceDraft<Record<string,unknown>>,key:string,invalidate?:(state:Record<string,unknown>)=>Record<string,unknown>):[Set<string>,Dispatch<SetStateAction<Set<string>>>] {
  const [values,setValues]=useWorkspaceField<string[]>(draft,key,[],invalidate);
  const value=useMemo(()=>new Set(values),[values]);
  const setter=useCallback<Dispatch<SetStateAction<Set<string>>>>(action=>setValues(previous=>Array.from(typeof action==='function'?action(new Set(previous)):action)),[setValues]);
  return [value,setter];
}
