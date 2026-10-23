"""Small public analysis surface backed by existing deterministic kernels."""

from __future__ import annotations

import json
from typing import Literal
from pydantic import Field

from agent_runtime.workspace import resolve_path
from tools import financial_analysis_tools as financial, reconcile_tool
from .contracts import Arguments, typed_tool
from .datasets import Dataset, validate_dataset_source


class RoleColumn(Arguments):
    role: Literal[
        "net_profit",
        "revenue",
        "total_assets",
        "equity",
        "current_assets",
        "current_liab",
        "inventory",
        "receivables",
        "cost",
        "interest",
        "cashflow",
        "payables",
    ]
    column: str = Field(min_length=1)


class Metrics(Dataset):
    kind: Literal["metrics"]
    columns: list[RoleColumn] = Field(min_length=1, max_length=12)
    metrics: list[str] = Field(default_factory=list, max_length=30)
    discount_rate: float = Field(default=0.1, gt=-1, le=10)


class Regression(Dataset):
    kind: Literal["regression"]
    x_columns: list[str] = Field(min_length=1, max_length=20)
    y_column: str = Field(min_length=1)
    add_constant: bool = True


class Correlation(Dataset):
    kind: Literal["correlation"]
    columns: list[str] = Field(min_length=2, max_length=30)
    method: Literal["pearson", "spearman"] = "pearson"
    threshold: float = Field(default=0.6, ge=0, le=1)


class TimeSeries(Dataset):
    kind: Literal["time_series"]
    date_column: str = Field(min_length=1)
    value_column: str = Field(min_length=1)
    freq: Literal["D", "W", "MS", "QS", "YS"] = "MS"
    decompose: bool = True
    forecast_horizon: int = Field(default=3, ge=1, le=60)
    method: Literal["auto", "linear", "moving_avg", "exp_smooth"] = "auto"


class NamedNumber(Arguments):
    name: str = Field(min_length=1)
    value: float | list[float]


class VariableRange(Arguments):
    name: str
    values: list[float] = Field(
        min_length=1,
        max_length=100,
        description="该变量的实际取值，例如销量 [400,500,600]",
    )


class Scenario(Arguments):
    name: str
    inputs: list[NamedNumber] = Field(min_length=1, max_length=30)


class Sensitivity(Arguments):
    kind: Literal["sensitivity"]
    base_inputs: list[NamedNumber] = Field(min_length=1, max_length=30)
    model_type: Literal["cvp", "npv", "custom_formula"] = "cvp"
    formula: str = Field(default="", max_length=1000)
    variable_ranges: list[VariableRange] = Field(default_factory=list, max_length=10)
    two_way_vars: list[str] = Field(default_factory=list, max_length=2)
    discount_rate: float = Field(default=0.1, gt=-1, le=10)
    scenarios: list[Scenario] = Field(default_factory=list, max_length=20)


class AnalyzeDataset(Arguments):
    request: Metrics | Regression | Correlation | TimeSeries | Sensitivity


class Reconcile(Arguments):
    file1: str
    file2: str
    sheet1: str = ""
    sheet2: str = ""
    match_keys: list[str] = Field(min_length=1, max_length=10)
    amount_col: str = Field(min_length=1)
    tolerance: float = Field(default=1, ge=0)
    merge_sheets2: bool = False
    audit_mode: bool = False
    skip_keys: list[str] = Field(default_factory=list, max_length=100)


class ChartFile(Arguments):
    chart_file: str = Field(
        description="授权工作目录内的 UTF-8 JSON 文件，包含 template/title/data/data_table，可选 option_overrides；结构见 Skill 模板"
    )


def _named(values):
    result = {value.name: value.value for value in values}
    if len(result) != len(values):
        raise ValueError("变量名不能重复")
    return result


def analyze_dataset(context, args):
    request = args.request
    if isinstance(request, Dataset):
        validate_dataset_source(context, request.file)
    kwargs = request.model_dump(exclude={"kind"})
    common = {"project_id": context.project_id, "work_dir": str(context.work_dir)}
    if isinstance(request, Metrics):
        kwargs.pop("columns")
        if len({column.role for column in request.columns}) != len(request.columns):
            raise ValueError("字段角色不能重复")
        kwargs.update(
            {f"{column.role}_col": column.column for column in request.columns}
        )
        return financial.financial_metrics(**kwargs, **common)
    if isinstance(request, Regression):
        return financial.regression_analysis(**kwargs, **common)
    if isinstance(request, Correlation):
        return financial.correlation_matrix(**kwargs, **common)
    if isinstance(request, TimeSeries):
        return financial.time_series_analysis(**kwargs, **common)
    kwargs["base_inputs"] = _named(request.base_inputs)
    kwargs["variable_ranges"] = {
        value.name: {"values": value.values} for value in request.variable_ranges
    }
    if len(kwargs["variable_ranges"]) != len(request.variable_ranges):
        raise ValueError("变量范围名称不能重复")
    for name in {*kwargs["variable_ranges"], *request.two_way_vars}:
        if not isinstance(kwargs["base_inputs"].get(name), (int, float)):
            raise ValueError("敏感性范围和双变量必须引用已有数值变量")
    kwargs["scenarios"] = {
        scenario.name: _named(scenario.inputs) for scenario in request.scenarios
    }
    return financial.sensitivity_analysis(**kwargs, **common)


def reconcile_variables(context, args):
    validate_dataset_source(context, args.file1)
    validate_dataset_source(context, args.file2)
    return reconcile_tool.reconcile_variables(
        **args.model_dump(),
        project_id=context.project_id,
        work_dir=str(context.work_dir),
    )


def generate_chart(context, args):
    path = resolve_path(context.work_dir, args.chart_file)
    if path.stat().st_size > 256000:
        raise ValueError("图表数据文件超过 256KB，请先聚合数据")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) - {
        "template",
        "title",
        "data",
        "data_table",
        "option_overrides",
    }:
        raise ValueError(
            "图表文件只接受 template/title/data/data_table/option_overrides"
        )
    return financial.generate_chart(**payload)


def specs():
    return [
        typed_tool(
            "analyze_dataset",
            "基于项目数据库执行财务指标、回归、相关性、时间序列或敏感性分析；参数按 kind 区分。",
            AnalyzeDataset,
            analyze_dataset,
            group="analysis",
            timeout=180,
        ),
        typed_tool(
            "reconcile_variables",
            "核对两份项目数据的匹配键和金额列；计算结果不直接修改源数据。",
            Reconcile,
            reconcile_variables,
            group="analysis",
            timeout=180,
        ),
        typed_tool(
            "generate_chart",
            "从图表 JSON 产物生成经模板校验的图表，保留已有全部模板。",
            ChartFile,
            generate_chart,
            group="analysis",
        ),
    ]
