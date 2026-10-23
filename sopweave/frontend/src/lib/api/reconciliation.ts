import type {ReportArtifact} from "./workbench";
/** reconciliation API contracts and requests. */
import { request } from './client';

// =====================================================================
// 集成应用 /api/apps（2026-08-20：混合模式——确定性核对 + 一次 LLM 报告）
// =====================================================================

export interface ReconVerifyParams {
  baseFile: string;
  detailFile: string;
  matchKeys: { k1: string; k2: string; k3: string; k2On: boolean; k3On: boolean };
  variables: string[];
  /** 依据表金额列（与 variables 第一项对应） */
  baseAmountCol: string;
  tolerance: number;
  unmatchedAction: string;
  autoFix: boolean;
}

/** 差异行（与页面 ReconDiff 兼容：字段宽松，含 index signature） */
export interface ReconDiffData {
  level: string;
  contract?: string;
  person?: string;
  client?: string;
  base_amount?: number | null;
  target_amount?: number | null;
  diff?: number | null;
  reason?: string;
  suggestion?: string;
  [k: string]: unknown;
}

export interface ReconDataPayload {
  summary: { ok: number; a1: number; a2: number; b: number; c: number };
  diffs: ReconDiffData[];
}

/** 待审计项（多轮审计循环：用户逐条决策 → 下一轮核对） */
export interface ReconAuditItem {
  key: string;
  /** base=总表独有 / detail=分表独有 / ambiguous=一对多模糊 */
  side: "base" | "detail" | "ambiguous" | string;
  reason: string;
  decision: string | null;
  candidates: number;
}

export interface ReconVerifyResult {
  report_artifact?:ReportArtifact;
  runId: string;
  report: string;
  reconData: ReconDataPayload;
  auditItems: ReconAuditItem[];
  raw: { summary: Record<string, unknown>; amount_col: unknown; match_keys: unknown };
}

/** 校验（确定性核对 + 一次 LLM 报告；差异表直出工具 JSON） */
export function reconVerify(
  projectId: string,
  modelId: string,
  params: ReconVerifyParams,
): Promise<ReconVerifyResult> {
  return request<ReconVerifyResult>("/api/apps/recon/verify", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, model_id: modelId, params }),
  });
}

/** 审计决策（多轮核对闭环：提交决策 → 下一轮核对） */
export interface ReconAuditDecision {
  key: string;
  side: string;
  decision: string;
}

export interface ReconAuditResult {
  runId: string;
  reconData: ReconDataPayload;
  auditItems: ReconAuditItem[];
  raw: { summary: Record<string, unknown> };
  /** 本轮实际应用的决策数（不含挂起） */
  applied: number;
  /** 剩余待审计项数 */
  remaining: number;
}

export function reconAudit(
  runId: string,
  projectId: string,
  params: ReconVerifyParams,
  decisions: ReconAuditDecision[],
): Promise<ReconAuditResult> {
  return request<ReconAuditResult>("/api/apps/recon/audit", {
    method: "POST",
    body: JSON.stringify({ run_id: runId, project_id: projectId, params, decisions }),
  });
}

/** 确定性写回结果（2026-08-21：写回不再依赖 LLM Agent 编排） */
export interface ReconWritebackResult {
  report_artifact?:ReportArtifact;
  written: number;
  skipped: number;
  export: { success: boolean; message?: string; error?: string; written_cells?: number };
  report: string;
}

export function reconWriteback(
  runId: string,
  projectId: string,
  params: ReconVerifyParams,
  diffs: ReconDiffData[],
): Promise<ReconWritebackResult> {
  return request<ReconWritebackResult>("/api/apps/recon/writeback", {
    method: "POST",
    body: JSON.stringify({ run_id: runId, project_id: projectId, params, diffs }),
  });
}
