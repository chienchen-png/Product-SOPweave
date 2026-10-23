# F1 现金预算/现金周期

## 目的
测算营运资金循环周期（CCC），预测现金需求，管理短期流动性。

## 关键公式
$$DSO = \frac{\text{平均应收账款}}{\text{年收入}} \times 365$$

$$DIO = \frac{\text{平均存货}}{\text{年销售成本}} \times 365$$

$$DPO = \frac{\text{平均应付账款}}{\text{年销售成本}} \times 365$$

$$\text{现金周期} CCC = DSO + DIO - DPO$$

## 变量含义
| 变量 | 含义 | 单位 | 方向 |
|------|------|------|------|
| 营业收入 | 同期间收入 | 元 | input |
| 营业成本 | 同期间营业成本 | 元 | input |
| 应收账款 | 期末应收账款余额 | 元 | input |
| 存货 | 期末存货余额 | 元 | input |
| 应付账款 | 期末应付账款余额 | 元 | input |
| DSO | 应收账款周转天数 | 天 | output |
| DIO | 存货周转天数 | 天 | output |
| DPO | 应付账款周转天数 | 天 | output |
| CCC | 现金周期 | 天 | output |

计算输入为营业收入、营业成本、应收账款、存货、应付账款。当前确定性工具统一采用期末余额、同期间年收入/成本与 365 天口径；与上面的平均余额口径比较时，应先准备平均余额列再映射。应付周转按营业成本估算，不能混称为已取得采购额。

## 适用场景
- 营运资金效率评估
- 现金需求预测
- 赊销/采购政策优化

## 注意事项
- 用 analyze_dataset（request.kind="metrics"、metrics=["ccc"]），将 revenue/cost/receivables/inventory/payables 五个角色映射到对应数据列。完整 CCC 由共享内核按 DSO+DIO−DPO 计算；缺少应付余额时输出已有周转天数与缺数说明，CCC 为 null。
- CCC 越长现金占用越多（负值 = 占用供应商资金）
- 与 F4 WCR 联动：CCC × 日成本 ≈ WCR
