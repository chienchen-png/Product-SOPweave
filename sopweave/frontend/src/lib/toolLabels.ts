const LABELS: Record<string,string> = {
  list_files:'浏览项目文件', search_text:'搜索文字', read_text:'读取文本', write_text:'写入文本', edit_text:'修改文本', run_process:'运行程序',
  ask_user:'询问用户', read_skill:'读取操作流程', prepare_skill_draft:'整理 SOP 草稿',
  inspect_workbook:'检查 Excel',read_sheet_range:'读取 Excel 区域',edit_workbook:'修改 Excel',read_document:'读取 Word',edit_document:'修改 Word',
  list_datasets:'浏览数据表',import_dataset:'导入数据',query_dataset:'查询数据',apply_data_changes:'修改数据库',export_dataset:'导出或写回 Excel',
  analyze_dataset:'财务分析',reconcile_variables:'核对金额差异',generate_chart:'生成图表',
};
export const toolLabel = (id:string) => LABELS[id] || id;
