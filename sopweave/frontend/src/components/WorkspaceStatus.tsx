import type {WorkspaceDraft} from '../hooks/useWorkspaceDraft';
import {useFeedback} from './ui/Feedback';

export default function WorkspaceStatus({draft}: {draft:WorkspaceDraft<Record<string,unknown>>}){
  const {confirm}=useFeedback();
  return <div className="workspace-status flex flex-wrap items-center gap-3 border-b">
    <span role="status">{!draft.ready?'恢复工作草稿…':draft.saving?'保存中…':draft.dirty?'尚未保存到服务端':'草稿已保存'}</span>
    {draft.dataChanged&&<span className="text-amber-700">数据已变化，旧计算结果已失效，请重新执行。</span>}
    {draft.error&&<><span role="alert" className="text-red-700">{draft.error}</span><button className="text-blue-700" disabled={draft.saving} onClick={()=>{if(!draft.ready)draft.reload();else void draft.saveNow();}}>{draft.ready?'重试保存':'重试加载'}</button>{draft.ready&&<button className="text-blue-700" onClick={()=>{void confirm('重新加载将用服务端草稿替换本页未保存输入，请先核对并复制需要保留的内容。').then(accepted=>{if(accepted)draft.reload();});}}>重新加载服务端草稿</button>}</>}
  </div>;
}
