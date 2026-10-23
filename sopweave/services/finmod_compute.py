"""Finmod compute application service."""
from __future__ import annotations
import logging
from typing import Any
import pandas as pd
from services.results import ServiceError, error, success
from storage.workbench_store import WorkbenchStore
from storage.connections import get_connection as get_db
from storage.project_data_store import ProjectDataStore
from storage.tmp_db import TmpRun, register_run, unregister_run
from tools.financial_analysis_tools import _resolve_chart_template

from services.app_data import _copy_project_data_to_tmp

logger = logging.getLogger(__name__)

def _run_models_parallel(specs: list[dict], df: pd.DataFrame,
                         params_map: dict[str, dict]) -> list[dict]:
    """多模型并行计算（线程池）；单模型失败不阻塞整体。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tools._finmod_models import run_model

    def _one(spec: dict) -> dict:
        model_id = spec["model_id"]
        return run_model(model_id, df, params_map.get(model_id, {}))

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, len(specs))) as pool:
        futs = {pool.submit(_one, s): s for s in specs}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001
                s = futs[fut]
                results.append({"model_id": s["model_id"], "code": s.get("code", "?"),
                                "name": s.get("name", ""), "ok": False, "summary": {"错误": str(e)}})
    # 按 model_ids 顺序稳定返回 + NaN→None（JSON 序列化安全）
    order = {s["model_id"]: i for i, s in enumerate(specs)}
    results.sort(key=lambda r: order.get(r["model_id"], 999))
    for r in results:
        r["summary"] = _clean_nan(r.get("summary", {}))
    return results


def _clean_nan(obj: Any) -> Any:
    """递归 NaN/inf → None（JSON 严格解析安全）。"""
    import math
    import numpy as np
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_nan(v) for v in obj]
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    return obj


def _model_params_with_period(
    params_map: dict[str, dict],
    model_ids: list[str],
    period_col: str,
) -> dict[str, dict]:
    """Apply the user's selected period column to every model execution."""
    result = {mid: dict(params_map.get(mid) or {}) for mid in model_ids}
    if period_col:
        for values in result.values():
            values["period_col"] = period_col
    return result


def _build_analysis_data_stats(
    df: pd.DataFrame,
    *,
    source_column_count: int,
    files: list[str],
    period_col: str,
) -> dict:
    """Summarize the user dataset without counting runtime alias columns."""
    num_cols = [c for c in df.columns[:12] if pd_to_numeric(df[c]).notna().sum() > 0]
    stats = {
        "rows": int(df.shape[0]),
        "columns": int(source_column_count),
        "files": files,
        "period_col": period_col,
    }
    for column in num_cols[:6]:
        values = pd_to_numeric(df[column]).dropna()
        if len(values) == 0:
            continue
        stats[str(column)] = {
            "均值": _round2(values.mean()),
            "中位": _round2(values.median()),
            "最小": _round2(values.min()),
            "最大": _round2(values.max()),
            "样本量": int(values.count()),
        }
    return stats


def finmod_run(p: dict):
    """确定性执行：配置清单 → TmpRun 快照 → 模型并行计算 → 结果 + 图表数据（0 LLM）。

    body: {project_id, file_name?, file_names?: [], model_ids,
           mappings: {model_id: {var: col}},
           params?: {model_id: {...}}, chart_manifest?: [...]}
    R1：file_names 传入（多文件）时自动跨文件合并成统一 DataFrame 再喂模型。
    Returns:
        {run_id, models: [{model_id, code, name, ok, summary}],
         charts: [{chart_id, template, variant, name, level, color_scheme, data_table}],
         merge_strategy, merge_logs}
    """
    project_id = (p.get("project_id") or "").strip()
    file_name = (p.get("file_name") or "").strip()
    file_names = p.get("file_names") or []
    if isinstance(file_names, str):
        file_names = [file_names]
    file_names = [f for f in file_names if f]
    if not file_names and file_name:
        file_names = [file_name]
    model_ids = list(p.get("model_ids") or [])
    mappings = p.get("mappings") or {}
    params = p.get("params") or {}
    period_col = str(p.get("period_col") or "").strip()
    manifest = p.get("chart_manifest") or []
    derived_formulas = p.get("derived_formulas") or []  # V1：派生/统计量公式（含统计量广播）
    prep_steps = p.get("prep_steps") or []  # V3：数据预处理步骤（清洗/合并/透视）
    if not project_id or not file_names:
        return error("缺少 project_id/file_name")
    if not model_ids:
        return error("缺少 model_ids")

    run = TmpRun.create()
    register_run(run.run_id, run)
    try:
        WorkbenchStore(get_db()).start_result('finmod',run.run_id,project_id,p)
        _copy_project_data_to_tmp(get_db(), run.db, project_id)
        tmp_store = ProjectDataStore(run.db)

        # R1：多文件 → 自动合并成统一 DataFrame（单文件走原逻辑）
        from tools.finmod_merge import merge_files_for_model
        merge_strategy = "single"
        merge_logs: list[str] = []
        merged_res = None
        if len(file_names) > 1:
            merged_res = merge_files_for_model(tmp_store, project_id, file_names,
                                               (p.get("sheet_name") or ""))
            merge_strategy = merged_res["strategy"]
            merge_logs = merged_res["logs"]
            base_df = merged_res["df"]
        else:
            base_df = None

        # V3：数据预处理（可选）——先变换再加载（pivot/merge 后列结构变化）
        from tools.finmod_eval import load_dataframe, materialize_derived
        prep_logs: list[str] = []
        if prep_steps:
            from tools.finmod_prep import run_prep_steps
            prep_res = run_prep_steps(tmp_store, project_id, file_names[0],
                                      (p.get("sheet_name") or ""), list(prep_steps),
                                      base_df=base_df)
            prep_logs = prep_res.get("logs") or []
            raw_df = prep_res.get("df")
        else:
            if base_df is not None:
                raw_df = base_df
            else:
                raw_df = load_dataframe(tmp_store, project_id, file_names[0],
                                        (p.get("sheet_name") or ""))
        # 所有模型所需变量并集
        all_vars: set[str] = set()
        for mid in model_ids:
            spec = _get_model_registry_spec(mid)
            if spec:
                all_vars.update(spec.get("required_vars") or [])
        # 构造模型变量 → 列映射（mappings 为 {model_id: {var: col}}；未映射列保持原名）
        alias: dict[str, str] = {}
        for mid, mp in mappings.items():
            for var, col in (mp or {}).items():
                alias[str(var)] = str(col)
        df = raw_df.copy()
        for var in all_vars:
            col = alias.get(var, var)
            if col in df.columns:
                continue
            # 未映射 → 尝试同名列
            if var in df.columns:
                df[var] = raw_df[var]
        # 重命名：模型变量名列已存在则直接用；别名映射的列复制到变量名
        for var, col in alias.items():
            if var in df.columns and col in df.columns and var != col:
                df[var] = raw_df[col]
            elif col in df.columns and var not in df.columns:
                df[var] = raw_df[col]

        # V1：物化派生/统计量列（统计量广播整列；列加入 df 供模型取用）
        derived_errors: list[dict] = []
        if derived_formulas:
            formulas = [str(f) for f in derived_formulas if str(f).strip()]
            if formulas:
                df, _dres, derived_errors = materialize_derived(df, formulas, alias)

        specs = [{"model_id": mid} for mid in model_ids]
        results = _run_models_parallel(
            specs,
            df,
            _model_params_with_period(params, model_ids, period_col),
        )

        # v3.6：收集每个模型的真正缺失变量（引擎 required_vars 中既不在 df、
        # 也无 alias 映射、也无派生公式覆盖的列）→ 附加到 result.missing_vars，
        # 供前端传「允许缺失分析」标记 & 报告「数据补充说明」节。
        for r in results:
            mid = r.get("model_id") or ""
            spec = _get_model_registry_spec(mid)
            req = list(spec.get("required_vars") or []) if spec else []
            mv = []
            for var in req:
                if var in df.columns:
                    continue
                if var in alias:
                    continue
                # 有派生公式覆盖（如 收入占比_i=...）则不算缺失
                if any(var in str(f) for f in derived_formulas):
                    continue
                mv.append(var)
            r["missing_vars"] = mv

        # 图表数据：从 df 提取（分类=首文本列或期间列；系列=模型结果摘要）
        charts_out: list[dict] = []
        for item in manifest:
            cid = item.get("id") or ""
            template = item.get("template") or ""
            variant = item.get("variant") or template
            name = item.get("name") or template
            level = item.get("level") or "L2"
            color_scheme = item.get("color_scheme") or "auto"
            # 数据口径：categories = 文本列/期间列，series = 数值列
            num_cols = [c for c in df.columns if pd_to_numeric(df[c]).notna().sum() > 0]
            cat_cols = [c for c in df.columns if c not in num_cols]
            categories = (df[cat_cols[0]].astype(str).tolist() if cat_cols else
                          [str(i + 1) for i in range(len(df))])[:20]
            base = _resolve_chart_template(template)[0]
            # 按模板类型适配数据结构（不同 build 函数期望不同格式）
            if base in ("pie", "funnel", "sunburst", "treemap", "gauge"):
                # {name, value} 对象（pie/sunburst/gauge 用 categories 作 name）
                first_val = num_cols[0] if num_cols else None
                data: dict = {
                    "categories": categories,
                    "series": [{
                        "name": first_val or "值",
                        "data": [
                            {"name": categories[i] if i < len(categories) else f"项{i + 1}",
                             "value": _round2(pd_to_numeric(df[first_val]).head(20).tolist()[i]) if first_val and i < len(df) else 0}
                            for i in range(min(len(df), 20))
                        ],
                    }],
                }
            elif base == "radar":
                # indicators = 数值列名；series[0].data[0].value = 各数值列首行值
                data = {
                    "indicators": [{"name": c, "max": float(pd_to_numeric(df[c]).max()) or 100}
                                   for c in num_cols[:6]],
                    "series": [{
                        "name": "指标",
                        "data": [{
                            "value": [_round2(pd_to_numeric(df[c]).head(20).tolist()[0]) if len(df) > 0 else 0
                                      for c in num_cols[:6]],
                        }],
                    }],
                }
            elif base in ("boxplot",):
                # 各数值列分布
                data = {
                    "categories": num_cols[:6],
                    "series": [{
                        "name": "分布",
                        "data": [[_round2(v) for v in pd_to_numeric(df[c]).dropna().tolist()[:20]]
                                 for c in num_cols[:6]],
                    }],
                }
            elif base in ("waterfall3d", "bar3d", "scatter3d"):
                # v6.9：3D 模板改用 Python(mplot3d) 生成 —— 需输出 Python `_norm_3d` 兼容数据结构：
                #   waterfall3d: xCategories(时间) + yCategories(指标) + series[0].data=[[xIdx,yIdx,zVal],...]
                #   bar3d/scatter3d: series[0].data=[[x,y,z],...]
                xc = categories[:12]                      # 时间/类别（x 轴）
                yc = num_cols[:10]                        # 指标（y 轴 waterfall3d）
                if base == "waterfall3d":
                    cells = []
                    for yi, col in enumerate(yc):
                        vals = pd_to_numeric(df[col]).head(len(xc)).tolist()
                        for xi, v in enumerate(vals):
                            cells.append([xi, yi, _round2(float(v)) if v is not None and pd.notna(v) else 0])
                    data = {
                        "xCategories": xc, "yCategories": yc,
                        "xAxisName": cat_cols[0] if cat_cols else "时间",
                        "yAxisName": "财务指标", "zAxisName": "数值",
                        "series": [{"name": "指标对比", "data": cells}],
                    }
                else:
                    # bar3d/scatter3d：数值坐标三元组 [[xIdx, yIdx, zVal], ...]
                    cells = []
                    for xi, _v in enumerate(xc):
                        for yi, col in enumerate(yc):
                            v = pd_to_numeric(df[col]).head(len(xc)).iloc[xi] if len(df) > xi else None
                            if v is not None and pd.notna(v):
                                cells.append([xi, yi, _round2(float(v))])
                    data = {
                        "xCategories": xc, "yCategories": yc,
                        "xAxisName": cat_cols[0] if cat_cols else "X",
                        "yAxisName": "指标", "zAxisName": "数值",
                        "series": [{"name": "三维", "data": cells}],
                    }
            else:
                # line/bar/bar-stacked/dual-axis/histogram/tornado/waterfall 等：数字数组
                series = []
                for c in num_cols[:6]:
                    series.append({"name": c, "data": [_round2(v) for v in pd_to_numeric(df[c]).head(20).tolist()]})
                data = {"categories": categories, "series": series}
            charts_out.append({
                "chart_id": cid,
                "template": template,
                "variant": variant,
                "name": name,
                "level": level,
                "color_scheme": color_scheme,
                "data": _clean_nan(data),
                "data_table": _clean_nan(df.head(20).to_dict("records")),
            })
        # R5：数据统计摘要（供报告「数据概览」节；AI 解读依据）
        data_stats: dict = {}
        source_column_count = int(raw_df.shape[1])
        try:
            data_stats = _build_analysis_data_stats(
                df,
                source_column_count=source_column_count,
                files=file_names,
                period_col=period_col,
            )
        except Exception as _dse:  # noqa: BLE001
            logger.warning("finmod/run data_stats 计算失败: %s", _dse)
            data_stats = {
                "rows": int(df.shape[0]),
                "columns": source_column_count,
                "files": file_names,
                "period_col": period_col,
            }
        response = {
            "run_id": run.run_id,
            "runId": run.run_id,
            "models": results,
            "charts": charts_out,
            "row_count": int(df.shape[0]),
            "derived_errors": derived_errors,  # V1：派生/统计量公式错误（不阻塞执行）
            "prep_logs": prep_logs,  # V3：数据预处理步骤日志（空=未用预处理）
            "merge_strategy": merge_strategy,  # R1：多文件合并策略（single/join/concat/union）
            "merge_logs": merge_logs,  # R1：合并日志
            "files_used": file_names,  # R1：参与执行的文件
            "data_stats": data_stats,  # R5：数据统计摘要（报告数据概览）
        }
        WorkbenchStore(get_db()).record_result('finmod',run.run_id,project_id,p,response)
        return success(response)
    except Exception as e:  # noqa: BLE001
        WorkbenchStore(get_db()).fail_result(run.run_id,str(e))
        unregister_run(run.run_id)
        run.cleanup()
        logger.exception("finmod/run 失败")
        if isinstance(e,ServiceError):raise
        return error(f"执行失败: {e}")


def finmod_analyze(p: dict):
    """数据分析器：formulas[] + 数据集定位 → _finmod_eval 求值 → 结果表（0 LLM）。

    body: {project_id, file_name?, file_names?: [], sheet_name?, formulas: []}
    R1：file_names 传入（多文件）时先合并成一个 DataFrame 再求值。
    Returns: {results, errors, columns, rows}
    """
    project_id = (p.get("project_id") or "").strip()
    file_name = (p.get("file_name") or "").strip()
    file_names = p.get("file_names") or []
    if isinstance(file_names, str):
        file_names = [file_names]
    file_names = [f for f in file_names if f]
    if not file_names and file_name:
        file_names = [file_name]
    formulas = list(p.get("formulas") or [])
    if not project_id or not file_names:
        return error("缺少 project_id/file_name")
    try:
        from tools.finmod_eval import eval_named_formulas, load_dataframe
        from tools.finmod_merge import merge_files_for_model
        store = ProjectDataStore(get_db())
        if len(file_names) > 1:
            merged = merge_files_for_model(store, project_id, file_names, (p.get("sheet_name") or ""))
            df = merged["df"]
        else:
            df = load_dataframe(store, project_id, file_names[0], (p.get("sheet_name") or ""))
        results, errors = eval_named_formulas([str(f) for f in formulas], df, limit=None)
        # 组装结果表：原列 + 派生列（series 类型），前 100 行
        out_rows: list[dict] = []
        derived_series = {k: v for k, v in results.items() if v["type"] == "series"}
        for i in range(min(len(df), 100)):
            row: dict = {}
            for c in df.columns[:10]:
                val = df.iloc[i][c]
                row[str(c)] = _round2(val) if _is_number(val) or _is_np_number(val) else str(val)
            for name, meta in derived_series.items():
                sample = meta["sample"]
                row[name] = sample[i] if i < len(sample) else None
            out_rows.append(row)
        scalars = {k: _round2(v["sample"]) for k, v in results.items() if v["type"] == "scalar"}
        return success({
            "results": results,
            "errors": errors,
            "columns": [str(c) for c in df.columns] + list(derived_series.keys()),
            "rows": out_rows,
            "scalars": scalars,
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/analyze 失败")
        return error(f"数据分析失败: {e}")


def _get_model_registry_spec(model_id: str) -> dict | None:
    from tools._finmod_models import MODEL_REGISTRY
    return MODEL_REGISTRY.get(model_id)


def pd_to_numeric(s) -> Any:
    import pandas as pd
    return pd.to_numeric(s, errors="coerce")


def _round2(v: Any) -> Any:
    import numpy as np
    if v is None:
        return None
    try:
        f = float(v)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, 2)
    except (TypeError, ValueError):
        return v


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_np_number(v: Any) -> bool:
    import numpy as np
    return isinstance(v, (np.integer, np.floating))
