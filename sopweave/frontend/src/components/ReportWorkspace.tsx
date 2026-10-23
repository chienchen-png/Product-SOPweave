import FileEditor from './FileEditor';
import {fileResourceBase,type ReportArtifact} from '../lib/api';

export default function ReportWorkspace({artifact,title='报告说明'}:{artifact:ReportArtifact;title?:string}){
  return <section className="ui-panel flex h-[75vh] min-h-[360px] flex-col overflow-hidden">
    <header className="border-b bg-zinc-50/60 px-5 py-4"><h3 className="ui-section-title">{title}</h3><p className="mt-1 text-xs text-zinc-500">可编辑报告说明；核对明细、计算结果与已执行变更以程序运行记录为准。</p></header>
    <FileEditor key={`${artifact.project_id}:${artifact.rel_path}`} projectId={artifact.project_id} filePath={artifact.rel_path} onClose={()=>{}} onSaved={()=>{}} embedded resourceBaseUrl={fileResourceBase(artifact.project_id)}/>
  </section>;
}
