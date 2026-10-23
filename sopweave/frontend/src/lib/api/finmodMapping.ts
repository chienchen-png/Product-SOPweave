/** finmodMapping API contracts and requests. */
import { request } from './client';

// =====================================================================
// 应用二 finmod — P3 变量映射（2026-08-21）
// =====================================================================
// 需求文档 §四 步骤 4：AI 预填映射 + 派生变量表达式 + 期间列识别。

/** 单个模型所需变量的映射行 */
export interface FinmodVarMapping {
  var_name: string;
  var_meaning: string;
  var_unit: string;
  /** 预填/用户选择的对应列（从文件列名选） */
  mapped_col: string;
  /** matched 已匹配 / missing 缺失（未选或未匹配）/ statistic 自动统计 / output 输出 */
  status: "matched" | "missing" | "statistic" | "output" | string;
  /** v2.0：变量方向 input 需映射 / statistic 统计量 / output 模型输出 */
  direction?: "input" | "statistic" | "output";
  /** v2.0：变量类型 numeric / category / period */
  var_type?: "numeric" | "category" | "period";
  /** v2.0：统计量/输出标记（direct/statistic/output） */
  suggested_mode?: "direct" | "statistic" | "output";
}

/** varmap 结果：每个模型的变量映射组 */
export interface FinmodVarmapModel {
  model_id: string;
  code: string;
  name: string;
  variables: FinmodVarMapping[];
}

/** GET/POST /api/apps/finmod/varmap 响应 data */
export interface FinmodVarmapResult {
  file: string;
  sheet: string;
  /** 文件全部列名（下拉选项） */
  all_cols: string[];
  /** 期间列候选（日期类型/列名含日期时间关键词） */
  period_cols: string[];
  models: FinmodVarmapModel[];
  missing_count: number;
  /** v2.0：输入变量总数（仅 direction=input） */
  input_count?: number;
  /** v2.0：统计量变量数 */
  statistic_count?: number;
  message: string;
  /** R1：参与映射的文件列表 */
  files?: string[];
  /** R1：列 → 来源文件列表（多文件时） */
  file_of_col?: Record<string, string[]>;
  /** R1：文件数 */
  file_count?: number;
}

/** V2 AI 变量匹配：单变量映射建议（LLM 推荐） */
export interface FinmodAiMapping {
  model_id: string;
  var_name: string;
  mapped_col: string;
  confidence: number;
  reason: string;
  candidates: string[];
}

/** V2 AI 统计量绑定：{stat: COUNT/MEAN/STD/..., based_on: 观测值变量} */
export interface FinmodAiStatBinding {
  model_id: string;
  var_name: string;
  stat: string;
  based_on: string;
}

/** v3.4：可派生变量的候选派生公式（内置公式库生成，供前端自动应用） */
export interface FinmodDerivedFormula {
  model_id: string;
  var_name: string;
  formula: string;
  based_on: string[];
  note: string;
}

/** POST /api/apps/finmod/varmap-suggest 响应 data */
export interface FinmodVarmapSuggestResult {
  varmap: FinmodVarmapResult;
  ai_suggestions: {
    mappings: FinmodAiMapping[];
    stat_bindings: FinmodAiStatBinding[];
  };
  /** v3.4：可派生变量候选公式（derivable 缺失变量 → 自动生成公式） */
  derived_formulas?: FinmodDerivedFormula[];
  used_llm: boolean;
}

export function finmodVarmap(
  projectId: string,
  fileName: string,
  modelIds: string[],
  sheetName?: string,
  fileNames?: string[],
): Promise<FinmodVarmapResult> {
  const body: Record<string, unknown> = {
    project_id: projectId,
    file_name: fileName,
    sheet_name: sheetName || "",
    model_ids: modelIds,
  };
  if (fileNames && fileNames.length > 0) body.file_names = fileNames;
  return request<FinmodVarmapResult>("/api/apps/finmod/varmap", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** V2 AI 变量匹配（LLM 推荐 + 置信度 + 统计量绑定；LLM 不可用降级确定性） */
export function finmodVarmapSuggest(
  projectId: string,
  fileName: string,
  modelIds: string[],
  llmModelId: string,
  sheetName?: string,
  fileNames?: string[],
): Promise<FinmodVarmapSuggestResult> {
  const body: Record<string, unknown> = {
    project_id: projectId,
    file_name: fileName,
    sheet_name: sheetName || "",
    model_ids: modelIds,
    model_id: llmModelId,
  };
  if (fileNames && fileNames.length > 0) body.file_names = fileNames;
  return request<FinmodVarmapSuggestResult>("/api/apps/finmod/varmap-suggest", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// =====================================================================
// 应用二 finmod — V3 数据预处理（2026-08-21）
// =====================================================================
// Stata/SPSS 式「分析就绪」：清洗 / 类型修正 / 多 sheet 合并 / 透视面板化。

/** 数据预处理步骤（四类 op） */
export interface FinmodPrepStep {
  /** clean | cast | merge | pivot */
  op: string;
  action?: string;
  col?: string;
  to?: string;
  subset?: string[];
  /** clean.filter/replace 条件 */
  where?: { col: string; op: string; value?: unknown };
  with?: unknown;
  /** merge 参数 */
  sheets?: string[];
  how?: string;
  on?: string[];
  suffixes?: string[];
  /** pivot 参数 */
  mode?: string;
  id_vars?: string[];
  var_name?: string;
  value_name?: string;
  index?: string;
  columns?: string;
  values?: string;
}

/** POST /api/apps/finmod/prep-data 响应 data */
export interface FinmodPrepResult {
  success: boolean;
  columns: { name: string; type: string }[];
  rows: number;
  preview: Record<string, unknown>[];
  logs: string[];
  errors: { step: number; op: string; message: string }[];
}

export function finmodPrepData(
  projectId: string,
  fileName: string,
  steps: FinmodPrepStep[],
  sheetName?: string,
  fileNames?: string[],
): Promise<FinmodPrepResult> {
  const body: Record<string, unknown> = {
    project_id: projectId,
    file_name: fileName,
    sheet_name: sheetName || "",
    steps,
  };
  if (fileNames && fileNames.length > 0) body.file_names = fileNames;
  return request<FinmodPrepResult>("/api/apps/finmod/prep-data", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 派生变量求值结果（每公式） */
export interface FinmodDerivedFormulaResult {
  type: "series" | "scalar" | string;
  /** 前 5 个示例值（series）或单值（scalar） */
  sample: unknown[];
  rows: number;
  dtype: string;
}

/** POST /api/apps/finmod/derived-eval 响应 data */
export interface FinmodDerivedEvalResult {
  results: Record<string, FinmodDerivedFormulaResult>;
  errors: { line: number; name: string; kind: string; message: string }[];
  all_cols: string[];
  row_count: number;
}

export function finmodDerivedEval(
  projectId: string,
  fileName: string,
  formulas: string[],
  mappings?: Record<string, string>,
  sheetName?: string,
  fileNames?: string[],
): Promise<FinmodDerivedEvalResult> {
  const body: Record<string, unknown> = {
    project_id: projectId,
    file_name: fileName,
    sheet_name: sheetName || "",
    formulas,
    mappings: mappings || {},
  };
  if (fileNames && fileNames.length > 0) body.file_names = fileNames;
  return request<FinmodDerivedEvalResult>("/api/apps/finmod/derived-eval", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
