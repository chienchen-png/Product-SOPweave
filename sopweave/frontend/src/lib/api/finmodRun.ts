/** finmodRun API contracts and requests. */
import type { FinmodChartManifestItem } from './finmodCharts';
import type { FinmodPrepStep } from './finmodMapping';
import { request } from './client';

// =====================================================================
// 应用二 finmod — P5 执行引擎（2026-08-21）
// =====================================================================
// 需求文档 §四 步骤 6：确定性执行（0 LLM）+ 数据分析器。

/** 模型执行结果 */
export interface FinmodRunModelResult {
  model_id: string;
  code: string;
  name: string;
  ok: boolean;
  summary: Record<string, unknown>;
}

/** 图表数据（run 返回，供 ChartRenderer 渲染） */
export interface FinmodRunChart {
  chart_id: string;
  template: string;
  variant: string;
  name: string;
  level: string;
  color_scheme: string;
  data: { categories: string[]; series: { name: string; data: unknown[] }[] };
  data_table: Record<string, unknown>[];
}

/** POST /api/apps/finmod/run 响应 data */
export interface FinmodRunResult {
  run_id: string;
  runId: string;
  models: FinmodRunModelResult[];
  charts: FinmodRunChart[];
  row_count: number;
  /** v2.0 V1：派生/统计量公式错误（不阻塞执行） */
  derived_errors?: { line: number; name: string; kind: string; message: string }[];
  /** V3：数据预处理步骤日志（空=未用预处理） */
  prep_logs?: string[];
  /** R1：多文件合并策略（single/join/concat/union） */
  merge_strategy?: string;
  /** R1：合并日志 */
  merge_logs?: string[];
  /** R1：参与执行的文件列表 */
  files_used?: string[];
  /** R5：数据统计摘要（均值/极值/样本量，供报告数据概览） */
  data_stats?: Record<string, unknown>;
}

export function finmodRun(
  projectId: string,
  fileName: string,
  modelIds: string[],
  mappings: Record<string, Record<string, string>>,
  params: Record<string, Record<string, unknown>>,
  chartManifest: FinmodChartManifestItem[],
  derivedFormulas: string[] = [],
  prepSteps: FinmodPrepStep[] = [],
  fileNames?: string[],
  periodCol = "",
): Promise<FinmodRunResult> {
  const body: Record<string, unknown> = {
    project_id: projectId,
    file_name: fileName,
    model_ids: modelIds,
    mappings,
    params,
    chart_manifest: chartManifest,
    derived_formulas: derivedFormulas,  // v2.0 V1：派生/统计量公式（统计量广播）
    prep_steps: prepSteps,              // V3：数据预处理步骤（清洗/合并/透视）
    period_col: periodCol,
  };
  if (fileNames && fileNames.length > 0) body.file_names = fileNames;
  return request<FinmodRunResult>("/api/apps/finmod/run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** POST /api/apps/finmod/analyze 响应 data */
export interface FinmodAnalyzeResult {
  results: Record<string, { type: string; sample: unknown[]; rows: number }>;
  errors: { line: number; name: string; kind: string; message: string }[];
  columns: string[];
  rows: Record<string, unknown>[];
  scalars: Record<string, unknown>;
}

export function finmodAnalyze(
  projectId: string,
  fileName: string,
  formulas: string[],
  fileNames?: string[],
): Promise<FinmodAnalyzeResult> {
  const body: Record<string, unknown> = { project_id: projectId, file_name: fileName, formulas };
  if (fileNames && fileNames.length > 0) body.file_names = fileNames;
  return request<FinmodAnalyzeResult>("/api/apps/finmod/analyze", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
