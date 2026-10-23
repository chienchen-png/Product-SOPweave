import type {DataFileRecord,WorkbenchTaskDetail} from './api';
export type DraftValues=Record<string,unknown>;
const FINMOD_OUTPUTS=['guideResult','selectedModels','varmap','mappingOverrides','periodCol','derivedFormulas','derivedResults','aiMappings','aiStatBindings','statBindings','prepSteps','prepResult','chartSuggest','chartManifest','runResult','resultRunId','reportResult','savedCharts'];
const RECON_OUTPUTS=['params','reconData','verifyReport','runId','auditItems','auditDecisions','auditDone','finalStatus','writebackDone','writebackReport','verifyArtifact','writebackArtifact'];
export function invalidateFinmod(value:DraftValues,stage=1):DraftValues {
  const keys=stage===1?FINMOD_OUTPUTS:stage===2?FINMOD_OUTPUTS.filter(key=>!['guideResult','selectedModels'].includes(key))
    :stage===3?['derivedResults','prepResult','chartSuggest','chartManifest','runResult','resultRunId','reportResult','savedCharts']
    :['runResult','resultRunId','reportResult','savedCharts'];
  const next={...value};keys.forEach(key=>delete next[key]);
  const old=value.workflow as {step:number;reached:number}|undefined;
  return {...next,workflow:{step:Math.min(old?.step||1,stage),reached:Math.min(old?.reached||1,stage)},...(stage===1?{macroPhase:'direction'}:{}),reportPhase:'execute'};
}
export function invalidateRecon(value:DraftValues,preserveStep=false):DraftValues {
  const next={...value};RECON_OUTPUTS.forEach(key=>delete next[key]);
  const previous=value.workflow as {step?:number}|undefined;
  return {...next,workflow:{step:preserveStep?Math.min(previous?.step||1,4):1,reached:4}};
}

export function restoreFinmodTask(previous:DraftValues,task:WorkbenchTaskDetail,files:DataFileRecord[]):DraftValues {
  const report=task.reports.find(artifact=>artifact.rel_path.endsWith('.md'));
  const names=(task.params.file_names||[task.params.file_name]) as string[];
  return {...invalidateFinmod(previous),runResult:task.result,
    selectedFiles:files.filter(file=>names.includes(file.file_name)),
    selectedModels:task.params.model_ids||[],mappingOverrides:task.params.mappings||{},chartManifest:task.params.chart_manifest||[],
    reportPhase:'report',reportResult:report?{...report,path:'',report:'',used_llm:!!report.used_llm}:null,
    workflow:{step:4,reached:4}};
}
