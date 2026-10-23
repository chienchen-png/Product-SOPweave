"""Static financial 3D charts backed by Matplotlib (PNG only).

Waterfall panels, histogram bars and scatter bubbles share normalized input data.
Chinese labels use installed system fonts; rendering does not require a browser.
"""
from __future__ import annotations

import base64
import io

import matplotlib
matplotlib.use("Agg")  # 无界面，输出 PNG
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection



# 中文字体（SimHei / Microsoft YaHei 已确认存在）
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ----------------------------------------------------------------------
# 数据归一化：把前端 chart-json 的 data 归一为绘图所需的结构
# ----------------------------------------------------------------------

def _norm_3d(data: dict) -> dict:
    """归一化 3D 数据。返回 {xc, yc, cells[[xIdx,yIdx,z]], axis names}。

    兼容两形态：
      - waterfall3d: data.xCategories/yCategories + series[0].data[[xIdx,yIdx,z]]
      - bar3d/scatter3d: data.series[0].data[[x,y,z]]（数值坐标）
    """
    xc = data.get("xCategories") or data.get("categories") or []
    yc = data.get("yCategories") or []
    series = data.get("series") or []
    s0 = series[0] if series else {}
    cells = s0.get("data") or []
    x_name = data.get("xAxisName") or "X"
    y_name = data.get("yAxisName") or "Y"
    z_name = data.get("zAxisName") or "Z"
    return {"xc": xc, "yc": yc, "cells": cells,
            "x_name": x_name, "y_name": y_name, "z_name": z_name}


def _blues(n: int, reverse: bool = False) -> list:
    """n 档 Blues 色（中间深→浅，等价 matplotlib Blues(linspace(0.9,0.3,n))）。"""
    vals = np.linspace(0.9, 0.3, n) if not reverse else np.linspace(0.3, 0.9, n)
    return [plt.cm.Blues(v) for v in vals]


def _png_bytes(fig) -> str:
    """fig → base64 PNG 字符串（无背景气泡）。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", transparent=False)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ----------------------------------------------------------------------
# 1) 立体瀑布图（复刻用户案例）
# ----------------------------------------------------------------------

def waterfall3d_png(data: dict) -> str:
    """每指标一条折线 + 从 z=0 基线到折线围成的竖直填充面。

    参考用户「财务指标对比.py」line_3d：for 每 i 构造 polygon
      [ [x0,y_i,0], [x_last,y_i,0], ...逆向 [x,y_i,z] ] → Poly3DCollection。
    x 年份在 x 轴，y 指标索引在 y 轴，z=数值。前(y=0)深后(yn-1)浅。
    """
    d = _norm_3d(data)
    xc, yc, cells = d["xc"], d["yc"], d["cells"]
    yn = len(yc)
    if yn == 0 or not cells:
        return _png_bytes(_empty_fig("无数据"))

    # 每指标按年份排列：[x_idx, z] 序列（按 x 排序）
    per_metric: dict[int, list] = {}
    for c in cells:
        xi, yi, z = int(c[0]), int(c[1]), float(c[2] or 0)
        per_metric.setdefault(yi, []).append((xi, z))
    for yi in per_metric:
        per_metric[yi].sort()

    colors = _blues(yn)  # 前深后浅
    x_vals = [i for i in range(len(xc))] if xc else []
    fig = plt.figure(figsize=(14, 9))
    ax = fig.add_subplot(111, projection="3d")

    zmax = max((c[2] or 0 for c in cells), default=1)

    for yi in range(yn):
        line = per_metric.get(yi, [])
        if not line:
            continue
        face_c = colors[yi]
        xs = [p[0] for p in line]
        zs = [p[1] for p in line]
        # 折线（黑色）
        ax.plot(xs, [yi] * len(xs), zs, color="black", lw=2, marker="o", ms=5, alpha=0.8)
        # z=0 基线（灰虚线）
        ax.plot(xs, [yi] * len(xs), [0] * len(xs), color="gray", ls="--", lw=1, alpha=0.6)
        # 填充面 polygon：底左→底右→逆序顶(回程)
        polygon = [[xs[0], yi, 0], [xs[-1], yi, 0]]
        for j in range(len(line) - 1, -1, -1):
            polygon.append([xs[j], yi, zs[j]])
        poly = Poly3DCollection([polygon], alpha=0.8)
        poly.set_facecolor(face_c)
        poly.set_edgecolor("black")
        poly.set_linewidth(0.5)
        ax.add_collection3d(poly)
        # 数值标注（每个数据点上方 f'{z:.2f}%'，加粗）
        for xi, zi in line:
            ax.text(xi, yi, zi + 0.5, f"{zi:.2f}%", color="black",
                    ha="center", va="bottom", fontsize=9, fontweight="bold")

    # 同一时间点不同变量的虚线连接（x_label_indexs = 全部 x 索引）
    x_indexs = list(range(len(xc))) if xc else []
    for k in x_indexs:
        time_k = [k] * yn
        var_k = list(range(yn))
        val_k = []
        for yi in range(yn):
            line = per_metric.get(yi, [])
            z = 0.0
            for xi, zi in line:
                if xi == k:
                    z = zi
                    break
            val_k.append(z)
        ax.plot(time_k, var_k, val_k, ls="--", lw=1.2, color="gray", alpha=0.7)

    # 坐标轴（加粗 + labelpad，复刻参考脚本）
    ax.set_xlabel(d["x_name"], fontsize=12, fontweight="bold", labelpad=12)
    ax.set_ylabel(d["y_name"], fontsize=12, fontweight="bold", labelpad=12)
    ax.set_zlabel(d["z_name"], fontsize=12, fontweight="bold", labelpad=12)
    if xc:
        ax.set_xticks(range(len(xc)))
        ax.set_xticklabels(xc, fontsize=11)
    if yc:
        ax.set_yticks(range(yn))
        ax.set_yticklabels(yc, fontsize=11)
    # 轴范围（复刻参考脚本；不含大标题——容器标题栏已展示表名，图内不重复表名）
    ax.set_xlim([min(x_vals) - 0.2, max(x_vals) + 0.2]) if x_vals else None
    ax.set_ylim([-0.5, yn - 0.5])
    ax.set_zlim(0, zmax * 1.15)
    ax.grid(True, linestyle="--", alpha=0.7)
    ax.view_init(elev=25, azim=45)
    plt.subplots_adjust(left=0.08, right=0.95, bottom=0.08, top=0.92)
    return _png_bytes(fig)


# ----------------------------------------------------------------------
# 2) 立体柱状图（v6.12：重构为「二维数据直方图」——柱高=频数，官网模板）
# ----------------------------------------------------------------------

def _hist2d(cells):
    """把数据归为二维点集 [[x,y],...]，做 histogram2d 得每格频数。

    兼容两形态：
      - bin_data 形态（推荐）：series[0].data = [[x,y],...]（二维点集，柱高=频数）
      - 三元组形态（向后兼容）：series[0].data = [[x,y,z],...]，取 (x,y) 频数
    返回 (xpos, ypos, dz, dx, dy, edges) —— 每根柱锚点 / 高 / 宽 / 网格边界。
    """
    pts = []
    for c in cells:
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            continue
        pts.append((float(c[0]), float(c[1])))
    if not pts:
        return [], [], [], [], [], None
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    # 自动选 bin 数：默认 4×4（官网模板），点量多时略增，但不超过 6×6 保证清晰
    n = 4 if len(pts) < 200 else 5 if len(pts) < 1000 else 6
    xmin, xmax, ymin, ymax = xs.min(), xs.max(), ys.min(), ys.max()
    if xmin == xmax:
        xmax = xmin + 1
    if ymin == ymax:
        ymax = ymin + 1
    hist, xe, ye = np.histogram2d(xs, ys, bins=n, range=[[xmin, xmax], [ymin, ymax]])
    xpos, ypos = np.meshgrid(xe[:-1], ye[:-1], indexing="ij")
    dx = (xe[1] - xe[0]) * 0.8
    dy = (ye[1] - ye[0]) * 0.8
    return (xpos.ravel().tolist(), ypos.ravel().tolist(), hist.ravel().tolist(),
            float(dx), float(dy), (xe, ye))


def bar3d_png(data: dict) -> str:
    """三维柱状图（官方 histogram2d 直方图模板）：柱高=频数，柱色随高度渐变。"""
    d = _norm_3d(data)
    cells = d["cells"]
    if not cells:
        return _png_bytes(_empty_fig("无数据"))
    xpos, ypos, dz, dx, dy, _ = _hist2d(cells)
    if not dz:
        return _png_bytes(_empty_fig("无数据"))
    zs = np.array(dz)
    cmap = plt.cm.Blues
    norm = matplotlib.colors.Normalize(vmin=zs.min(), vmax=zs.max() or 1)
    colors = [cmap(norm(z)) for z in zs]

    fig = plt.figure(figsize=(11, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.bar3d(xpos, ypos, [0] * len(zs), np.full(len(zs), dx),
             np.full(len(zs), dy), zs, color=colors, alpha=0.92,
             edgecolor="black", linewidth=0.3, zsort="average")
    ax.set_xlabel(d["x_name"], fontsize=12, fontweight="bold", labelpad=10)
    ax.set_ylabel(d["y_name"], fontsize=12, fontweight="bold", labelpad=10)
    ax.set_zlabel("频数", fontsize=12, fontweight="bold", labelpad=10)
    ax.view_init(elev=25, azim=45)
    plt.subplots_adjust(left=0.08, right=0.95, bottom=0.08, top=0.92)
    return _png_bytes(fig)


# ----------------------------------------------------------------------
# 3) 立体散点图
# ----------------------------------------------------------------------

def scatter3d_png(data: dict) -> str:
    """三维散点图：scatter3d 数据 [[x,y,z],...]，气泡色=第 3 维。

    v6.12：支持大数据量（≥1000 点），气泡减小避免过于拥挤；色=第 3 维。
    """
    d = _norm_3d(data)
    cells = d["cells"]
    if not cells:
        return _png_bytes(_empty_fig("无数据"))
    xs = [float(c[0]) for c in cells]
    ys = [float(c[1]) for c in cells]
    zs = [float(c[2] or 0) for c in cells]
    n = len(xs)
    size = max(8, min(45, 4000 // max(n, 1)))  # 点越多越小（1000 点→4px）

    fig = plt.figure(figsize=(11, 8))
    ax = fig.add_subplot(111, projection="3d")
    sc = ax.scatter(xs, ys, zs, c=zs, cmap="Blues", s=size, alpha=0.75,
                    edgecolor="black", linewidth=0.2)
    fig.colorbar(sc, ax=ax, shrink=0.6, pad=0.1)
    ax.set_xlabel(d["x_name"], fontsize=12, fontweight="bold", labelpad=10)
    ax.set_ylabel(d["y_name"], fontsize=12, fontweight="bold", labelpad=10)
    ax.set_zlabel(d["z_name"], fontsize=12, fontweight="bold", labelpad=10)
    ax.view_init(elev=25, azim=45)
    plt.subplots_adjust(left=0.08, right=0.95, bottom=0.08, top=0.92)
    return _png_bytes(fig)


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------

_FONT_STACK = "SimHei, 'Microsoft YaHei', 'Segoe UI', sans-serif"










# ----------------------------------------------------------------------
# 分发
# ----------------------------------------------------------------------

_PY_CHART_FNS = {
    "waterfall3d": waterfall3d_png,
    "bar3d": bar3d_png,
    "scatter3d": scatter3d_png,
}



def render_py_chart(template: str, data: dict) -> dict:
    """Render one static PNG; the UI consumes no secondary interactive payload."""
    fn = _PY_CHART_FNS.get(template)
    if not fn:
        return {"success": False, "error": f"Python 图表不支持: {template}"}
    try:
        b64 = fn(data)
        out: dict = {"success": True, "b64": b64,
                     "note": "由 Python 生成 · 3D 图不支持矢量导出"}
        return out
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Python 绘图失败: {e}"}


def _empty_fig(msg: str):
    fig = plt.figure(figsize=(6, 4))
    ax = fig.add_subplot(111)
    ax.text(0.5, 0.5, msg, ha="center", va="center")
    ax.axis("off")
    return fig
