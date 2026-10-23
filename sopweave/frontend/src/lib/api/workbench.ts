import {request} from './client';
export type WorkspaceKind='chat'|'recon'|'finmod';
export interface WorkspaceDocument<T> {state:T;revision:number;data_revision:string;saved_data_revision:string;updated_at?:string}
const draftPath=(project:string,kind:WorkspaceKind)=>`/api/workbench/drafts/${encodeURIComponent(project)}/${kind}`;
export const fetchWorkspaceDraft=<T>(project:string,kind:WorkspaceKind,signal?:AbortSignal)=>request<WorkspaceDocument<T>>(draftPath(project,kind),{signal});
export const saveWorkspaceDraft=<T>(project:string,kind:WorkspaceKind,state:T,revision:number,dataRevision:string)=>request<WorkspaceDocument<T>>(draftPath(project,kind),{method:'PUT',body:JSON.stringify({state,expected_revision:revision,data_revision:dataRevision})});

export interface ReportArtifact {run_id:string;project_id:string;rel_path:string;filename:string;version?:string;used_llm?:boolean}
export interface WorkbenchTask {run_id:string;project_id:string;project_name:string;kind:string;title:string;status:string;created_at:string;updated_at:string;error:string}
export interface WorkbenchTaskDetail extends WorkbenchTask {result:Record<string,unknown>|null;params:Record<string,unknown>;reports:ReportArtifact[];writeback:Record<string,unknown>|null;data_revision:string;legacy_note?:{report:string;params:Record<string,unknown>}|null}
export const listWorkbenchTasks=(options:{project_id?:string;status?:string;cursor?:string;limit?:string}={})=>request<{tasks:WorkbenchTask[];next_cursor:string}>(`/api/workbench/tasks?${new URLSearchParams(options)}`);
export const fetchWorkbenchTask=(id:string)=>request<WorkbenchTaskDetail>(`/api/workbench/tasks/${encodeURIComponent(id)}`);
export const listWorkbenchReports=(projectId='')=>request<{reports:(ReportArtifact&{created_at:string;goal:string})[]}>(`/api/workbench/reports?${new URLSearchParams({project_id:projectId})}`);
export const hideWorkbenchReport=(report:ReportArtifact)=>request('/api/workbench/reports',{method:'DELETE',body:JSON.stringify({project_id:report.project_id,run_id:report.run_id,rel_path:report.rel_path})});
export const fetchReconHistory=<T>(projectId:string)=>request<{records:T[]}>(`/api/workbench/history/recon?${new URLSearchParams({project_id:projectId})}`);
export const importLegacyHistory=(projectId:string,kind:'recon'|'finmod',records:Record<string,unknown>[])=>request<{imported:number;skipped:string[]}>('/api/workbench/legacy-history',{method:'POST',body:JSON.stringify({project_id:projectId,kind,records})});
