/** finmodGuide API contracts and requests. */
import { request } from './client';

// =====================================================================
// 应用二 finmod — P2 引导引擎（2026-08-21）
// =====================================================================
// 建议型引导（需求文档 §2.1/§四 步骤 2）：诉求 + 列名索引 → LLM 建议 JSON。

/** 引导方向建议（模式 B：模型推荐卡） */
export interface FinmodDirection {
  model_id: string;
  code: string;
  name: string;
  /** AI 推荐理由（白话） */
  reason: string;
  /** 教学文案（白话讲解 + KaTeX 公式，Markdown） */
  teaching: string;
  /** 推荐图表模板名（如 radar/tornado/line） */
  recommended_charts: string[];
  /** 数据完备性（v3.2：步骤 2 建模方向确认时用统一精确口径评估，含可派生/真缺拆分） */
  var_coverage?: {
    /** 已匹配 input 变量数 */
    matched: number;
    /** 真正未匹配且不可派生的 input 变量数（数据不足） */
    missing: number;
    /** 统计量变量数（自动计算，不算缺失） */
    statistic: number;
    /** input 变量总数（不含 statistic/output） */
    input_vars: number;
    /** 真正缺失、需补数据的变量名列表 */
    missing_names: string[];
    /** 是否就绪（无 truly_missing 即 True，可放行建模） */
    ready: boolean;
    /** 缺失但可由已有数值列派生的变量名列表 */
    derivable: string[];
    /** 真正缺失、需补数据的变量名列表 */
    truly_missing: string[];
    /** 可派生变量详情（含含义/单位，v3.3） */
    derivable_details?: { var_name: string; meaning: string; unit: string; var_type: string }[];
    /** 真正缺失变量详情（含含义/单位 → 建议补什么数据，v3.3） */
    truly_missing_details?: { var_name: string; meaning: string; unit: string; var_type: string }[];
    /** 人类可读结论 */
    suggestion: string;
    /** 是否有数据文件 */
    has_file: boolean;
  };
}

/** 数据需求项（模式 A：无数据时的字段清单） */
export interface FinmodDataNeed {
  field: string;
  type: string;
  example: string;
  purpose: string;
  suggested_chart: string;
}

/** POST /api/apps/finmod/guide 响应 data */
export interface FinmodGuideResult {
  success: boolean;
  /** A 数据需求 / B 已有数据 / C 知识讲解（确定性判定，LLM 不覆盖） */
  mode: "A" | "B" | "C" | string;
  goal: string;
  message: string;
  directions: FinmodDirection[];
  data_needs: FinmodDataNeed[];
  /** 模式 C 讲解引导文案（Markdown） */
  explanation: string;
}

export function finmodGuide(
  projectId: string,
  modelId: string,
  goal: string,
  fileNames?: string[],
): Promise<FinmodGuideResult> {
  const body: Record<string, unknown> = { project_id: projectId, model_id: modelId, goal };
  if (fileNames && fileNames.length > 0) body.file_names = fileNames;
  return request<FinmodGuideResult>("/api/apps/finmod/guide", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
