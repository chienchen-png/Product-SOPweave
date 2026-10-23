/** finmodCharts API contracts and requests. */
import { request } from './client';

// =====================================================================
// 应用二 finmod — P4 图表配置（2026-08-21）
// =====================================================================
// 需求文档 §四 步骤 5：AI 推荐图表 + 档位白名单 + chart_manifest 收敛。

/** 图表推荐项（POST /charts-suggest） */
export interface FinmodChartSuggestion {
  template: string;
  name: string;
  level: string;
  level_label: string;
  default_variant: string;
  reason: string;
  /** R4：重要性 primary 首选 / secondary 次选 / optional 兜底 */
  importance?: "primary" | "secondary" | "optional";
  /** R4：图表类别（trend/compare/share/sensitivity/distribution/relation） */
  category?: string;
  /** R4：类别中文名 */
  category_label?: string;
}

/** POST /api/apps/finmod/charts-suggest 响应 data */
export interface FinmodChartSuggestResult {
  level: string;
  level_label: string;
  suggestions: FinmodChartSuggestion[];
  note: string;
  /** R4：可用图表类别（中文） */
  categories?: string[];
}

/** R4：图表风格偏好（用户表达，非硬性勾选） */
export interface FinmodStylePrefs {
  /** 复杂度档位 L1/L2/L3 */
  level?: string;
  /** 配色 auto/blues/greens/reds/oranges/purples/blacks */
  colors?: string;
  /** 偏好图表类别（trend 趋势/compare 对比/share 占比/sensitivity 敏感性/distribution 分布/relation 关系） */
  categories?: string[];
}

export function finmodChartSuggest(
  modelIds: string[],
  guideCharts?: string[],
  level?: string,
  stylePrefs?: FinmodStylePrefs,
): Promise<FinmodChartSuggestResult> {
  const body: Record<string, unknown> = {
    model_ids: modelIds,
    guide_charts: guideCharts || [],
    level: level || "L2",
  };
  if (stylePrefs) body.style_prefs = stylePrefs;
  return request<FinmodChartSuggestResult>("/api/apps/finmod/charts-suggest", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** chart_manifest 单项（步骤 5 确认清单收敛，P5 run 消费） */
export interface FinmodChartManifestItem {
  id: string;
  /** 模板名（类型，如 line） */
  template: string;
  /** 变体名（如 line-forecast / line-smooth；默认变体=template） */
  variant: string;
  /** 模板中文名 */
  name: string;
  /** 复杂度档位 L1/L2/L3 */
  level: string;
  /** 可用变体列表（下拉） */
  variants: string[];
  /** 配色（宏观选择写入；auto/blues/greens/reds/oranges/purples/blacks） */
  color_scheme: string;
  /** 数据源列（数据口径） */
  data_source: string[];
  /** 推荐理由（来自 charts-suggest） */
  reason?: string;
  /** R4：重要性（primary 优先使用 / secondary 按需 / optional 兜底；AI 报告可增减） */
  importance?: "primary" | "secondary" | "optional";
}
