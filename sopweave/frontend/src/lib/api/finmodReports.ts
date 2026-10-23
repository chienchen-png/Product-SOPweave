/** finmodReports API contracts and requests. */
import { request } from './client';
import type { FinmodRunModelResult } from './finmodRun';
import type { FinmodRunChart } from './finmodRun';
import type { FinmodStylePrefs } from './finmodCharts';

// =====================================================================
// 应用二 finmod — P6 报告交付（2026-08-21）
// =====================================================================
// 需求文档 §四 步骤 7 + §六：SVG/PNG 落盘 → 静态代理 → md 报告（LLM/降级）。

/** POST /api/apps/finmod/save-chart 响应 data */
export interface FinmodSaveChartResult {
  path: string;
  rel_path: string;
  filename: string;
}

export function finmodSaveChart(
  projectId: string,
  runId: string,
  filename: string,
  content: string,
  format: "svg" | "png" = "svg",
): Promise<FinmodSaveChartResult> {
  return request<FinmodSaveChartResult>("/api/apps/finmod/save-chart", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, run_id: runId, filename, content, format }),
  });
}

/** POST /api/apps/finmod/py-chart 响应 data（Python 3D 图 → base64 PNG 静态图） */
export interface FinmodPyChartResult {
  b64: string;
  note: string;
  success: boolean;

}

/** Python 3D 图（瀑布/柱状/散点）：data → mplot3d → base64 PNG 静态图。
 * 3D 模板（waterfall3d/bar3d/scatter3d）改用 Python 生成；前端只显示 <img> 静态图
 * （图片样例），点开可放大/下载（PythonImageLightbox）；不再用 plotly.js 渲染交互。 */
export function finmodPyChart(
  template: string,
  data: Record<string, unknown>,
): Promise<FinmodPyChartResult> {
  return request<FinmodPyChartResult>("/api/apps/finmod/py-chart", {
    method: "POST",
    body: JSON.stringify({ template, data }),
  });
}

/** POST /api/apps/finmod/export-md 响应 data */
export interface FinmodExportMdResult {
  path: string;
  rel_path: string;
  filename: string;
  report: string;
  used_llm: boolean;
}

export function finmodExportMd(params: {
  projectId: string;
  runId: string;
  goal: string;
  modelId: string;
  models: FinmodRunModelResult[];
  charts: (FinmodRunChart & { rel_path?: string })[];
  /** R5：数据统计摘要（run 返回） */
  dataStats?: Record<string, unknown>;
  /** R5：图表风格偏好 */
  stylePrefs?: FinmodStylePrefs;
}): Promise<FinmodExportMdResult> {
  const body: Record<string, unknown> = {
    project_id: params.projectId,
    run_id: params.runId,
    goal: params.goal,
    model_id: params.modelId,
    models: params.models,
    charts: params.charts,
  };
  if (params.dataStats) body.data_stats = params.dataStats;
  if (params.stylePrefs) body.style_prefs = params.stylePrefs;
  return request<FinmodExportMdResult>("/api/apps/finmod/export-md", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** 图表资源预览 URL（静态代理） */
export function finmodAssetUrl(runId: string, file: string): string {
  return `/api/apps/finmod/files/${runId}/assets/${encodeURIComponent(file)}`;
}

/** D5：应用二报告 Markdown → Word 导出（后端 Pandoc）。content 传入则优先（当前编辑内容）。 */
export async function finmodExportDocx(runId: string, content?: string): Promise<Blob> {
  const resp = await fetch("/api/apps/finmod/export-docx", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId, content }),
  });
  if (!resp.ok) {
    let msg = `导出失败（HTTP ${resp.status}）`;
    try {
      const j = await resp.json();
      if (j && typeof j.message === "string") msg = j.message;
    } catch { /* 非 JSON 响应保留默认信息 */ }
    throw new Error(msg);
  }
  return await resp.blob();
}
