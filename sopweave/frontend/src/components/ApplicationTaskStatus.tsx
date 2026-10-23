import {useEffect,useRef,useState} from 'react';
import {fetchWorkbenchTask,listWorkbenchTasks,type WorkbenchTask,type WorkbenchTaskDetail} from '../lib/api';
import {useFeedback} from './ui/Feedback';

/** Discover server-owned work after navigation or browser restart. */
export default function ApplicationTaskStatus({projectId,currentRunId,localBusy,dataRevision,onActive,onRestore}:{projectId:string;currentRunId:string;localBusy:boolean;dataRevision:string;onActive:(active:boolean)=>void;onRestore:(task:WorkbenchTaskDetail)=>void|Promise<void>}){
  const feedback=useFeedback();
  const [task,setTask]=useState<WorkbenchTask|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const activeCallback=useRef(onActive);activeCallback.current=onActive;
  useEffect(()=>{
    let disposed=false,inflight=false;
    const read=async()=>{
      if(inflight)return;inflight=true;
      try{const page=await listWorkbenchTasks({project_id:projectId,limit:'1'});if(!disposed){const latest=page.tasks[0]||null;setTask(latest);setError('');activeCallback.current(!!latest&&['starting','running','resuming'].includes(latest.status));}}
      catch(cause){if(!disposed)setError((cause as Error).message);}finally{inflight=false;}
    };
    void read();const timer=window.setInterval(()=>void read(),3000);
    return()=>{disposed=true;window.clearInterval(timer);};
  },[projectId,localBusy]);
  if(!task&&!error)return null;
  const running=!!task&&['starting','running','resuming'].includes(task.status);
  if(!error&&!running&&(localBusy||task?.run_id===currentRunId))return null;
  return <div className="application-status flex flex-wrap items-center gap-3 border-b">
    <span className="mr-auto">{error|| (running?'任务正在当前应用工作区执行；完成或离开后，可返回此页面恢复结果。':task?.status==='waiting'?'上次任务正在等待你确认，请返回对应流程继续处理。':task?.status==='completed'?'当前应用有已完成的任务，可恢复参数、结果和报告。':'上次任务未完成；当前输入已保留，可重新执行。')}</span>
    {task?.status==='completed'&&<button disabled={busy} className="ui-button" onClick={async()=>{
      if(!await feedback.confirm('恢复此任务的参数与计算结果，将替换当前工作草稿。继续吗？'))return;
      setBusy(true);try{const detail=await fetchWorkbenchTask(task.run_id);if(detail.data_revision&&detail.data_revision!==dataRevision)throw new Error('导入数据已变化，旧结果仍保存在历史记录中；当前工作区需重新计算。');if(detail.project_id!==projectId)throw new Error('任务不属于当前应用工作区');await onRestore(detail);setError('');}catch(cause){setError((cause as Error).message);}finally{setBusy(false);}
    }}>{busy?'恢复中…':'恢复任务结果'}</button>}
  </div>;
}
