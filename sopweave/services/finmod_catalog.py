"""Read-only model and chart catalog."""
from __future__ import annotations
import logging
from services import finmod_refs
from services.results import error, success
logger=logging.getLogger(__name__)

def finmod_meta(p: dict):
    """模型目录（32 个 A-G 分类）+ 图表目录（24 模板 L1/L2/L3 档位）。"""
    try:
        return success(finmod_refs.get_meta())
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/meta 失败")
        return error(f"元数据加载失败: {e}")


def finmod_model_detail(p: dict, model_id: str):
    """单模型详情（目的/公式/变量表/适用场景）——<id> 经 references 映射解析。

    支持 id 形态：a1-three-statement（完整文件名）或 a1（编号前缀）。
    """
    try:
        data = finmod_refs.get_model_detail(model_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/models/%s 失败", model_id)
        return error(f"模型详情解析失败: {e}")
    if data is None:
        return error(f"未找到模型「{model_id}」；可用编号见 /finmod/meta", 404)
    return success(data)


def finmod_chart_detail(p: dict, chart_id: str):
    """单图表模板详情（用途/数据结构/选型建议/变体）。"""
    try:
        data = finmod_refs.get_chart_detail(chart_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/charts/%s 失败", chart_id)
        return error(f"图表详情解析失败: {e}")
    if data is None:
        return error(f"未找到图表模板「{chart_id}」；可用模板见 /finmod/meta", 404)
    return success(data)
