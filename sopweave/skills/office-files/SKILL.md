---
name: office-files
description: 查看、识别和精确修改 Excel、Word 与文本文档。触发词：Excel、Word、工作表、单元格、文档、DOCX。
user-invocable: true
toolsets: [office, project-data]
---
# Office 文件

- inspect_workbook 查看工作表、结构、公式缓存及文件 version。
- read_sheet_range 使用 sheet、cell_range（如 A1:D30）读取坐标化单元格；最多 2000 格，按需开启 formulas。重复表头按原坐标保留，不猜测字段结构。
- 正式数据分析使用 import_dataset 和 query_dataset，复用布局识别、复核、来源与快照。
- import_dataset 返回 needs_anchor 时，用 ask_user 确认从 1 开始的表头行号后重试；返回 needs_layout_review 时，引导用户在项目数据导入界面确认区域和列，不猜测布局或用 Python 绕过复核。
- edit_workbook 提交 expected_version 和 changes；每项有 sheet/cell/value/kind。不存在的 sheet 会创建；kind=formula 才将值写成公式。
- read_document 按块读取 DOCX 正文、表格和页眉页脚，返回 block ID 与 version；truncated=true 时用 next_offset、next_character_offset 分别作为 offset、character_offset 续读，超长段落不会跳过剩余文字。
- edit_document 使用 expected_version 和 changes，每项指定 block_id、old_text、new_text；原文应唯一匹配。保留 run 格式，复杂域、修订、嵌入对象无法保真时报告工具限制。
- 批量写入尽量一次提交；版本冲突重新读取。Excel 公式缓存缺失应重算或复核，不能把空缓存当作零。
- 文件读写权限由工具统一处理，不为同一动作再弹一次业务确认。
