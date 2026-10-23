---
name: financial-modeling
description: 财务建模、指标分析、预测、敏感性、情景分析与图表，或解释模型公式。触发词：建模、预测、预算、杜邦、回归、相关性、趋势、NPV、IRR、图表。
user-invocable: true
toolsets: [project-data, analysis, office]
---
# 财务建模 Skill

## 工作方法

- 解释概念时读取 references/index.md 或 references/models/<编号>.md，不读取用户数据。
- 基于数据计算时先 list_datasets；缺少的数据用 import_dataset，已导入数据直接复用。
- 明确分析目标和字段映射，复用已有答案；只将缺少的业务假设组成 ask_user 问题组。图表颜色和复杂程度可使用合理默认，不强制弹窗。
- query_dataset 负责筛选、聚合和连接，返回有限样本与 revision；所有来源在报告中标注。
- analyze_dataset.request 按 kind 选择 metrics/regression/correlation/time_series/sensitivity；工具复用既有计算内核。
- 长尾模型先读取对应模型资源，必要时用 run_process 执行 Python，对明确的只读快照进行计算，并输出脚本及结果产物。该 Skill 不允许修改数据库数据或源工作簿。
- 分析结果先有表格和业务解释，再按需要绘图。常用模型无需逐项反复确认。

## 资源与图表

使用 read_skill(skill_id="financial-modeling", resource_path="references/models/a1-three-statement.md") 读取模型定义；图表结构见 references/charts/<模板名>.md，风格见 references/chart-style.md。

模型与图表资源提供口径及数据结构；参数示例见 references/tool-examples.md，调用时以当前工具 schema 为准。

generate_chart 接受 chart_file，文件是工作目录内的 UTF-8 JSON 产物，结构为：

```json
{"template":"bar","title":"收入对比","data":{"categories":["本期","上期"],"series":[{"name":"收入","data":[100,80]}]},"data_table":[{"期间":"本期","收入":100},{"期间":"上期","收入":80}]}
```

可以通过 write_text 创建小型图表文件；大型图表由 Python 从计算结果生成。返回的模板图表由前端展示，不需要在回复中重复输出整段 JSON。保留表格依据，不能编造图表数值。

## 报告

输出 Markdown，包括输入与来源、数据口径、方法及参数、结果表、图表解读、结论和缺失信息。用户可在现有编辑器编辑及导出报告。
