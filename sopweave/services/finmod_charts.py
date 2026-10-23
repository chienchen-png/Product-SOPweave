"""Finmod charts application service."""
from __future__ import annotations
import logging
from services import finmod_refs
from services.results import error, success
from tools.financial_analysis_tools import _resolve_chart_template


logger = logging.getLogger(__name__)

_MODEL_CHART_PREFS: dict[str, list[str]] = {
    # A 报表预测
    "a1-three-statement": ["bar", "line"],
    "a2-income-forecast": ["line", "waterfall"],
    "a3-cashflow-forecast": ["line", "bar"],
    "a4-consolidation": ["bar", "pie"],
    # B 预算预测
    "b1-budget": ["dual-axis", "gauge"],
    "b2-rolling-forecast": ["line", "dual-axis"],
    "b3-sales-forecast": ["line", "error-bar"],
    "b4-cost-budget": ["bar", "waterfall"],
    # C 成本盈利
    "c1-cvp-breakeven": ["line", "waterfall"],
    "c2-contribution-margin": ["bar", "waterfall"],
    "c3-cost-variance": ["tornado", "waterfall"],
    "c4-product-profitability": ["bar", "treemap"],
    # D 绩效比率
    "d1-dupont": ["radar", "bar"],
    "d2-financial-ratios": ["radar"],
    "d3-budget-variance": ["waterfall", "bar"],
    "d4-yoy-mom": ["bar", "line"],
    "d5-leverage": ["bar"],
    # E 资本决策
    "e1-npv-irr": ["bar", "error-bar"],
    "e2-wacc": ["gauge"],
    "e3-sensitivity": ["tornado", "heatmap"],
    "e4-scenario": ["bar", "radar"],
    "e5-project-breakeven": ["line", "bar"],
    "e6-equipment-replacement": ["bar", "waterfall"],
    # F 营运资金
    "f1-cash-cycle": ["line", "bar"],
    "f2-aging-analysis": ["bar", "pie"],
    "f3-inventory-turnover": ["line", "bar"],
    "f4-wcr": ["line", "bar"],
    # G 统计量化
    "g1-descriptive-stats": ["boxplot", "histogram"],
    "g2-correlation": ["heatmap", "scatter"],
    "g3-regression": ["scatter", "line"],
    "g4-time-series-decompose": ["line", "bar"],
    "g5-forecasting": ["line", "error-bar"],
}


LEVEL_ALLOWED = {"L1": ["L1"], "L2": ["L1", "L2"], "L3": ["L1", "L2", "L3"]}


CHART_CATEGORIES: dict[str, str] = {
    "line": "trend", "dual-axis": "trend", "error-bar": "trend", "calendar": "trend",
    "bar": "compare", "tornado": "compare", "boxplot": "distribution",
    "pie": "share", "funnel": "share", "sunburst": "share", "treemap": "share", "sankey": "share",
    "radar": "compare", "heatmap": "relation", "scatter": "relation", "graph": "relation",
    "waterfall": "compare", "gauge": "compare", "parallel": "compare",
    "histogram": "distribution",
    "scatter3d": "relation", "bar3d": "compare", "waterfall3d": "compare", "histogram-4grid": "distribution",
}


CHART_CATEGORY_LABELS = {
    "trend": "趋势", "compare": "对比", "share": "占比",
    "sensitivity": "敏感性", "distribution": "分布", "relation": "关系",
}


def _finmod_chart_suggest(model_ids: list[str], guide_charts: list[str] | None = None,
                          level: str = "L2",
                          style_prefs: dict | None = None) -> dict:
    """图表推荐（确定性）：guide 推荐优先 + 模型→模板映射补齐 → 档位白名单过滤。

    R4（2026-08-23）：支持 style_prefs 偏好 + 重要性标注。
      - style_prefs: {level?: "L1"|"L2"|"L3", colors?: str, categories?: [str]}
        categories 取 CHART_CATEGORY_LABELS 键（trend/compare/share/sensitivity/
        distribution/relation）——命中偏好的图表 importance 升一级。
      - importance: primary（guide 推荐 / 模型首选）/ secondary（模型映射补齐）/
        optional（兜底 或 与偏好类别不符）。

    Returns:
        {level, level_label, categories, suggestions: [{template, name, level,
          level_label, default_variant, reason, importance, category, category_label}], note}
    """
    prefs = style_prefs or {}
    level = (str(prefs.get("level") or level or "L2")).upper()
    if level not in LEVEL_ALLOWED:
        level = "L2"
    allowed = set(LEVEL_ALLOWED[level])
    pref_cats = [str(c) for c in (prefs.get("categories") or []) if c]

    charts_meta = {c["id"]: c for c in finmod_refs.scan_charts()}
    ordered: list[tuple[str, str, str]] = []  # (template, reason, source)
    seen: set[str] = set()

    # 1) guide 推荐优先（LLM 已按诉求定制）→ primary
    for t in guide_charts or []:
        t = (t or "").strip()
        base = _resolve_chart_template(t)[0]
        if base in charts_meta and charts_meta[base]["level"] in allowed and t not in seen:
            ordered.append((t, "AI 根据你的诉求推荐", "primary"))
            seen.add(t)

    # 2) 模型 → 模板映射补齐（首选 primary，其余 secondary）
    for mid in model_ids:
        prefs_list = _MODEL_CHART_PREFS.get(mid) or []
        for idx, t in enumerate(prefs_list):
            if t in charts_meta and charts_meta[t]["level"] in allowed and t not in seen:
                imp = "primary" if idx == 0 else "secondary"
                ordered.append((t, "与所选模型分析类型匹配", imp))
                seen.add(t)

    # 3) 兜底：档位内默认模板（至少 1 张）→ optional
    if not ordered:
        for t, meta in charts_meta.items():
            if meta["level"] in allowed:
                ordered.append((t, "默认推荐", "optional"))
                break

    suggestions = []
    for t, reason, source_imp in ordered:
        base = _resolve_chart_template(t)[0]
        meta = charts_meta.get(base)
        if not meta:
            continue
        # R4：图表类别（用于偏好过滤）
        category = CHART_CATEGORIES.get(base, "compare")
        # 偏好类别命中 → importance 升一级（optional→secondary→primary）；
        # 未命中 → secondary 降为 optional（primary 模型首选保留）。
        importance = source_imp
        if pref_cats:
            if category in pref_cats:
                if importance == "optional":
                    importance = "secondary"
                elif importance == "secondary":
                    importance = "primary"
            else:
                if importance == "secondary":
                    importance = "optional"
        # 阶段 3：官方级变体偏好（v6.6）——推荐该类型最接近官方代表图的变体：
        # 如 bar → rounded（官方 bar-gradient 圆角渐变观感）、line → area（官方
        # 渐变面积）、scatter → effect（官方涟漪）；默认变体保持纯类型名。
        var_pref = {
            "bar": "", "line": "", "pie": "donut",
            "scatter": "effect", "radar": "multi", "heatmap": "discrete",
            "waterfall": "bar", "boxplot": "multi", "sankey": "", "gauge": "progress",
            "treemap": "drilldown", "graph": "force", "funnel": "compare",
            "calendar": "year", "dual-axis": "", "sunburst": "", "tornado": "",
            "parallel": "", "histogram": "", "error-bar": "range", "histogram-4grid": "",
            "waterfall3d": "", "scatter3d": "", "bar3d": "",
        }.get(base, "")
        suggested = f"{base}-{var_pref}" if var_pref else base
        suggestions.append({
            "template": t,  # 保持原始（含 guide 变体如 line-forecast）
            "name": meta["name"],
            "level": meta["level"],
            "level_label": meta["level_label"],
            "default_variant": suggested,
            "reason": reason,
            "importance": importance,  # R4：primary/secondary/optional
            "category": category,  # R4：trend/compare/share/...
            "category_label": CHART_CATEGORY_LABELS.get(category, category),  # R4：中文
        })
    return {
        "level": level,
        "level_label": finmod_refs.CHART_LEVELS.get(level, ""),
        "categories": list(CHART_CATEGORY_LABELS.values()),  # R4：可用类别
        "suggestions": suggestions,
        "note": (f"档位白名单生效：{level} 任务仅显示 {', '.join(sorted(allowed))} 档模板；"
                 f"推荐默认采用官方级变体（大数据量 + 渐变/动画，对齐 ECharts 官方示例）"
                 + (f"；已按偏好类别过滤：{', '.join(CHART_CATEGORY_LABELS.get(c, c) for c in pref_cats)}" if pref_cats else "")),
    }


def finmod_charts_suggest(p: dict):
    """图表推荐：模型 + guide 推荐 + 风格偏好 → 模板清单（确定性，无 LLM）。

    body: {model_ids: [], guide_charts?: [template], level?: "L1"|"L2"|"L3",
           style_prefs?: {level?, colors?, categories?: [str]}}
    """
    model_ids = p.get("model_ids") or []
    guide_charts = p.get("guide_charts") or []
    level = p.get("level") or "L2"
    style_prefs = p.get("style_prefs") or {}
    if not model_ids:
        return error("缺少 model_ids")
    try:
        return success(_finmod_chart_suggest(
            list(model_ids), list(guide_charts), level, style_prefs))
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/charts-suggest 失败")
        return error(f"图表推荐失败: {e}")


def finmod_py_chart(p: dict):
    """Python 3D 图（瀑布/柱状/散点）：data → matplotlib mplot3d → base64 PNG。

    背景（2026-08-23）：echarts-gl 无法还原「每行折线围成填充平面」的 3D 瀑布图，
    且 3D 无法矢量导出。用户决策：立体瀑布/散点/柱状改用 Python(mplot3d) 生成 PNG。

    body: {template: "waterfall3d"|"bar3d"|"scatter3d", data: chart-json}
    Returns: {b64, note, success}
    """
    template = (p.get("template") or "").strip()
    data = p.get("data") or {}
    if not template:
        return error("缺少 template")
    from tools.finmod_py_charts import render_py_chart
    return success(render_py_chart(template, data))
