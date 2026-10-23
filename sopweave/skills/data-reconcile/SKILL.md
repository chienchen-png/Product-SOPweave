---
name: data-reconcile
description: 财务数据核对。匹配两表、检查金额差异、分级并形成可追溯修正报告。触发词：核对、差异、比对、对不上。
user-invocable: true
toolsets: [project-data, analysis, office]
---
# 数据核对 Skill

1. 用户提到项目数据库时直接使用 list_datasets，不要用 list_files 或 run_process 搜索 `.db/.sqlite`。空参数返回全部文件、分表行数和列数；记录数直接引用该目录，不逐表查询。字段按目标文件读取：传 `file` 与 `include_fields=true`，或传 `file` 与 `sheet` 读取单表。未导入的工作目录文件使用 import_dataset。出现公式/布局复核请求时，将缺少的信息交给用户，不能猜测。
2. 明确双方文件、表、匹配键、金额列、容差。复用用户已提供的信息；其余信息用一次 ask_user 问题组收集。选择题使用稳定问题/选项 ID。
3. 只有需要少量样本、筛选或聚合时使用 query_dataset；不要用 query_dataset 代替目录行数。相同名称不等于同一字段，依据字段索引和来源坐标核实。
4. 使用 reconcile_variables 计算差异，复用 references/matching-examples.md 中的业务判断。计算层不直接写数据。
5. 先报告匹配、未匹配、差额与来源；用户明确要求修正后，query_dataset 获取最新 revision，apply_data_changes 使用条件和值列表事务修改。权限由实际写工具审批一次完成。
6. export_dataset 将变更写回；精确模式与删除/模板重建的差异由工具返回信息判断。原文件被占用、版本冲突或公式缓存缺失时停止相应写入并报告。
7. 输出 Markdown 报告，包括数据版本、匹配规则、差异汇总、已执行修正和待处理项。金额、等级及容差判断以计算工具结果为准；建议调整容差时，必须先用新参数重新核对，不能猜测差异会降级。差异方向仅说明数值关系，未查证原因时不能据此认定哪方多计或少计。

## 参数约定

- query_dataset.filters 示例：[{"field":"编号","value":"001"}]。
- apply_data_changes：operation 为 update/insert/delete；expected_revision 来自最近查询。update 使用 filters 和 values；insert 使用 rows，每行带 values 列表；delete 必须有 filters。
- read_sheet_range 按 A1 范围读取原表，不以表头作为返回对象键。
- 禁止反复确认已知信息、逐项弹出无必要的问题、无条件修改整表或以 Python 绕过拒绝。
