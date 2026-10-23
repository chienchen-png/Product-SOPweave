# 匹配示例

先用 list_datasets 查看两份文件的工作表和字段，从双方都存在的列中选择匹配键。优先合同编号、订单号，再结合人员或客户。金额列名不同时使用“依据列->目标列”映射。

## 合同级核对

以下是 reconcile_variables 的参数示例；文件名和字段名应替换为实际索引结果。

```json tool=reconcile_variables
{"file1":"依据.xlsx","file2":"目标.xlsx","match_keys":["合同编号","销售人员"],"amount_col":"应提金额->已兑现金额","tolerance":1}
```

## 汇总与明细核对

依据表每名销售人员一行，目标表每人多份合同，双方都具有“销售人员”列时可用该列匹配。工具会在候选身份一致时聚合金额后比较；身份不一致或无法唯一确定时保留待复核项。

目标文件按人员拆分到多个 sheet 且结构一致时，使用 merge_sheets2=true。匹配不上的记录保留清单供复核；需要其他匹配口径时，先明确业务规则，再通过 query_dataset 聚合/连接核实。

核对只产生结果；修正数据使用 apply_data_changes，写回使用 export_dataset。
