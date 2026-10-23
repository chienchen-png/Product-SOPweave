/** finmodCatalog API contracts and requests. */
import { request } from './client';

// =====================================================================
// 应用二「财务建模与分析」— finmod 元数据层（P1，2026-08-21）
// =====================================================================
// 数据源：financial-modeling skill references（.github/skills/financial-modeling/references）
// 端点：GET /api/apps/finmod/meta | /models/<id> | /charts/<id>

/** 模型元信息（meta 目录项） */
export interface FinmodModelMeta {
  /** 文件名 stem（如 a1-three-statement） */
  id: string;
  /** 编号（如 A1） */
  code: string;
  name: string;
  /** 分类键（A-G） */
  category: string;
  category_label: string;
}

/** 图表模板元信息（meta 目录项） */
export interface FinmodChartMeta {
  /** 模板 id（如 line） */
  id: string;
  name: string;
  /** 复杂度档位：L1/L2/L3 */
  level: "L1" | "L2" | "L3" | string;
  level_label: string;
  /** 可用变体名列表（如 line-smooth/line-area…） */
  variants: string[];
}

/** 模型分类分组（meta 的 model_categories 项） */
export interface FinmodModelGroup {
  key: string;
  label: string;
  models: FinmodModelMeta[];
}

/** 图表档位分组（meta 的 chart_levels 项） */
export interface FinmodChartGroup {
  key: string;
  label: string;
  charts: FinmodChartMeta[];
}

/** GET /api/apps/finmod/meta 响应 */
export interface FinmodMetaData {
  model_categories: FinmodModelGroup[];
  models: FinmodModelMeta[];
  chart_levels: FinmodChartGroup[];
  charts: FinmodChartMeta[];
  counts: { models: number; charts: number };
}

/** markdown 分节（详情通用） */
export interface FinmodSection {
  heading: string;
  content: string;
}

/** 模型详情（GET /api/apps/finmod/models/<id>） */
export interface FinmodModelDetail {
  id: string;
  code: string;
  name: string;
  category: string;
  category_label: string;
  /** 目的（「目的/用途」节） */
  purpose: string;
  /** 关键公式（$$...$$ 块，KaTeX 渲染） */
  formulas: string[];
  /** 变量含义表（[{变量, 含义, 单位}]） */
  variables: Record<string, string>[];
  /** 适用场景列表 */
  scenarios: string[];
  /** 注意事项列表 */
  notes: string[];
  /** 完整分节（保序，供逐节渲染） */
  sections: FinmodSection[];
  /** 原始 markdown 全文 */
  raw: string;
}

/** 图表详情（GET /api/apps/finmod/charts/<id>） */
export interface FinmodChartDetail {
  id: string;
  name: string;
  level: string;
  level_label: string;
  purpose: string;
  scenarios: string[];
  /** 数据结构示例（json 代码块） */
  data_structure: string;
  selection: string[];
  /** 变体清单（含主变体 + 别名 + 图表名 + 用途） */
  variants: { variant: string; aliases: string[]; chart: string; usage: string }[];
  notes: string[];
  sections: FinmodSection[];
  raw: string;
}

export function finmodMeta(): Promise<FinmodMetaData> {
  return request<FinmodMetaData>("/api/apps/finmod/meta");
}

export function finmodModelDetail(modelId: string): Promise<FinmodModelDetail> {
  return request<FinmodModelDetail>(
    `/api/apps/finmod/models/${encodeURIComponent(modelId)}`,
  );
}

export function finmodChartDetail(chartId: string): Promise<FinmodChartDetail> {
  return request<FinmodChartDetail>(
    `/api/apps/finmod/charts/${encodeURIComponent(chartId)}`,
  );
}
