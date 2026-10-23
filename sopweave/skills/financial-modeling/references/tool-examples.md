# 财务分析工具参数示例

代码块标明工具名，内部只包含该工具的参数。文件名、列名、假设和路径按实际任务替换。先用 list_datasets 确认字段；query_dataset 获取有限样本或聚合，不重复导入有效数据。

## 指标分析

```json tool=analyze_dataset
{"request":{"kind":"metrics","file":"报表.xlsx","columns":[{"role":"revenue","column":"收入"},{"role":"net_profit","column":"净利润"}],"metrics":["ratios"]}}
```

## CVP 敏感性

变量和值使用 name/value 列表；可从结果读取 one_way 和 break_even。

```json tool=analyze_dataset
{"request":{"kind":"sensitivity","model_type":"cvp","base_inputs":[{"name":"price","value":100},{"name":"vc","value":60},{"name":"fc","value":10000},{"name":"qty","value":500}],"variable_ranges":[{"name":"qty","values":[400,500,600]}]}}
```

## 情景分析

每个情景提供完整的输入假设。

```json tool=analyze_dataset
{"request":{"kind":"sensitivity","model_type":"cvp","base_inputs":[{"name":"price","value":100},{"name":"vc","value":60},{"name":"fc","value":10000},{"name":"qty","value":500}],"scenarios":[{"name":"悲观","inputs":[{"name":"price","value":90},{"name":"vc","value":65},{"name":"fc","value":10000},{"name":"qty","value":400}]}]}}
```

## 图表

先用 write_text 或 run_process 把 template/title/data/data_table 写入 UTF-8 JSON 文件。本目录 charts/*.md 中的“数据结构”是文件的 data 字段；color_scheme 也放在 data 中。

```json tool=generate_chart
{"chart_file":"artifacts/收入对比.json"}
```

generate_chart 返回图表结构交由前端展示，报告引用结果和依据表格。
