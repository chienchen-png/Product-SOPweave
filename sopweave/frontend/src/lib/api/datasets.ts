/** datasets API contracts and requests. */
import { request } from './client';

export interface DataTask {
  id: string;
  kind: "import" | "export";
  project_id: string;
  path?: string;
  status: "running" | "needs_anchor" | "needs_review" | "failed" | "done";
  progress?: number;
  error?: string;
}

export interface WorkbookSheetInfo {
  name: string;
  state?: string;
  hidden_rows?: number[];
  hidden_row_count?: number;
  hidden_columns?: string[];
  hidden_column_count?: number;
}

export function listProjectTasks(projectId: string): Promise<{ tasks: DataTask[] }> {
  return request(`/api/project-data/tasks?project_id=${encodeURIComponent(projectId)}`);
}

// =====================================================================
// 项目数据 /api/project-data
// =====================================================================

export interface DataFileRecord {
  id: string;
  project_id: string;
  source_path: string;
  file_name: string;
  file_hash: string;
  format: string;
  sheet_count: number;
  total_rows: number;
  status: string;
  error_message: string | null;
  meta_json: string | null;
  created_at: string;
  updated_at: string;
  import_version?: number;
  needs_reimport?: boolean;
  sheets?: DataSheet[];
  meta?: {
    workbook_manifest?: {sheets?: WorkbookSheetInfo[]};
    file_size?: number;
    formulas_materialized?: boolean;
    layouts?: Record<string, SheetLayout>;
    uncorrected_cols?: Record<string, number[]>;
    col_audit?: string;
    validation?: {
      status?: "ok" | "warning";
      skipped_sheets?: { name: string; classification: string }[];
      sheets?: Record<string, { status: string; issues: { code: string; [key: string]: unknown }[] }>;
    };
  };
  // Phase 17 P5：每文件变更计数（列表角标）
  changes_count?: { total: number; update: number; insert: number; delete: number };
}

export interface DataSheet {
  file_id: string;
  sheet_name: string;
  sheet_index: number;
  row_count: number;
  col_count: number;
  headers: {
    name: string;
    original_name?: string;
    type: string;
    type_confidence?: number;
    type_counts?: Record<string, number>;
    source_col?: number;
    non_null_rate?: number;
    samples?: string[];
    excel_col?: string;  // Phase 17 P6：Excel 列字母（A/B/C…）
  }[];
}

// Phase 17 P6：表头识别布局（index 接口透传 meta.layouts）
export interface SheetLayout {
  header_row?: number;
  excel_header_row?: number;  // 1-based 表头行（直接显示）
  data_start_row?: number;    // 后端持久化的 0-based 数据起始行
  excel_data_start_row?: number;
  data_end_row?: number;
  start_col?: number;
  end_col?: number;
  excluded_columns?: number[];
  classification?: "table" | "report" | "notes" | "empty";
  region_source?: string;
  confidence?: number;
  anchored_by?: "auto" | "user";
}

// Phase 17 P5：字段级修改日志（写回剧本）
export interface ChangeSummary {
  total: number;
  update: number;
  insert: number;
  delete: number;
}

export interface ChangeLogItem {
  applied?: boolean;
  id: number;
  file_id: string;
  sheet_name: string;
  row_index: number;      // 数据表 id
  col_index: number | null;
  col_name: string | null;  // 字段名（日志固化）
  key_value: string | null; // 行定位键（主键列值，日志固化）
  old_value: string | null;
  new_value: string | null;
  op_type: "update" | "insert" | "delete";
  created_at: string;
  excel_row: number | null; // Excel 物理行（header_row+1+序号）
  excel_col: string | null; // Excel 列字母（A/B/C…）
}

export function listDataFiles(projectId: string): Promise<{ files: DataFileRecord[] }> {
  return request<{ files: DataFileRecord[] }>(
    `/api/project-data/list?project_id=${encodeURIComponent(projectId)}`,
  );
}

export function importDataFile(
  projectId: string,
  path: string,
  opts: { alwaysAnchor?: boolean } = {},
): Promise<{ import_id: string }> {
  return request<{ import_id: string }>("/api/project-data/import", {
    method: "POST",
    body: JSON.stringify({
      project_id: projectId,
      path,
      always_anchor: opts.alwaysAnchor ?? false,
    }),
  });
}

export function importStatus(importId: string): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    `/api/project-data/import/status?import_id=${encodeURIComponent(importId)}`,
  );
}

/** 提交表头行锚点后继续导入（Phase 14E：低置信度需人工确认表头行）。
 * anchors: {sheet_name: 0-based 表头行}——前端弹窗用 1-based UI，提交前 -1。 */
export function importAnchor(
  importId: string,
  anchors: Record<string, number | {
    header_row: number;
    data_start_row: number;
    data_end_row: number;
    start_col: number;
    end_col: number;
    excluded_columns: number[];
  }>,
): Promise<{ import_id: string; resumed: boolean }> {
  return request<{ import_id: string; resumed: boolean }>("/api/project-data/import/anchor", {
    method: "POST",
    body: JSON.stringify({ import_id: importId, anchors }),
  });
}

export function resumeImportCalculation(
  importId: string,
  strategy: "cached" | "recalculate",
): Promise<{ import_id: string; resumed: boolean; strategy: string }> {
  return request("/api/project-data/import/calculation", {
    method: "POST",
    body: JSON.stringify({ import_id: importId, strategy }),
  });
}

export function dataIndex(fileId: string): Promise<{ file: DataFileRecord; sheets: DataSheet[]; changes: unknown[] }> {
  return request<{ file: DataFileRecord; sheets: DataSheet[]; changes: unknown[] }>(
    `/api/project-data/${encodeURIComponent(fileId)}/index`,
  );
}

export function dataChanges(
  fileId: string,
  opts: { limit?: number; offset?: number; sheet?: string; op?: string } = {},
): Promise<{ changes: ChangeLogItem[]; summary: ChangeSummary; pending_summary?: ChangeSummary; has_more: boolean }> {
  const p = new URLSearchParams({ limit: String(opts.limit ?? 200) });
  if (opts.offset) p.set("offset", String(opts.offset));
  if (opts.sheet) p.set("sheet", opts.sheet);
  if (opts.op) p.set("op", opts.op);
  return request<{ changes: ChangeLogItem[]; summary: ChangeSummary; pending_summary?: ChangeSummary; has_more: boolean }>(
    `/api/project-data/${encodeURIComponent(fileId)}/changes?${p.toString()}`,
  );
}

export function exportData(
  projectId: string,
  file: string,
  mode = "precise",
): Promise<{ export_id: string }> {
  return request<{ export_id: string }>("/api/project-data/export", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, file, mode }),
  });
}

// Phase 17 P7：写回任务进度轮询（贴边框进度条）
export interface ExportStatus {
  status: "running" | "done" | "failed";
  progress: number;
  message: string;
  written_cells: number;
  error: string | null;
  error_type?: string | null;  // Phase 17 P9：file_locked 等结构化错误类型
}

export function exportStatus(exportId: string): Promise<ExportStatus> {
  return request<ExportStatus>(
    `/api/project-data/export/status?export_id=${encodeURIComponent(exportId)}`,
  );
}

export function deleteDataFile(fileId: string): Promise<unknown> {
  return request<unknown>(`/api/project-data/${encodeURIComponent(fileId)}`, {
    method: "DELETE",
  });
}
