/** files API contracts and requests. */
import { request } from './client';

// =====================================================================
// 文件 /api/files
// =====================================================================

export interface FileEntry {
  name: string;
  path: string;
  size: number | null;
  type: "directory" | "file";
  updated_at?: number;
}

export interface FileTreeResponse {
  path: string;
  entries: FileEntry[];
  warnings?: string[];
  total?: number;
  next_cursor?: string;
}

export function fetchFileTree(
  projectId: string,
  path = "",
  limit = 500,
  offset = 0,
): Promise<FileTreeResponse> {
  return request<FileTreeResponse>(
    `/api/files/tree?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}&limit=${limit}&offset=${offset}`,
  );
}

export interface FileEntryFull {
  name: string;
  path: string;
  size: number | null;
  type: "directory" | "file";
  updated_at?: number;
}

export function readFileContent(projectId: string, path: string): Promise<{ file: FileEntryFull; content: string; version: string }> {
  return request<{ file: FileEntryFull; content: string; version: string }>(
    `/api/files/read?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`,
  );
}

/** D3：文件元信息（含 viewer_type / can_edit / can_open_external，双击分发依据） */
export interface FileMeta extends FileEntryFull {
  viewer_type: string;
  can_edit: boolean;
  can_open_external: boolean;
}

export function fileMeta(projectId: string, path: string): Promise<{ file: FileMeta }> {
  return request<{ file: FileMeta }>(
    `/api/files/meta?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}`,
  );
}

/** D3：调本机程序打开文件（office 双击直接本地打开） */
export function openExternalFile(
  projectId: string,
  path: string,
): Promise<{ file: FileMeta; opened: boolean; message: string }> {
  return request<{ file: FileMeta; opened: boolean; message: string }>("/api/files/open-external", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, path }),
  });
}

/** D3：raw 二进制流 URL（pdf/图片浏览器预览） */
export function rawFileUrl(projectId: string, path: string, binary = true): string {
  return `/api/files/raw?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(path)}${binary ? "&binary=1" : ""}`;
}

/** Native Markdown render base; path segments cannot become query parameters. */
export function fileResourceBase(projectId: string, directory = ""): string {
  const encoded = directory.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  return `/api/files/resource/${encodeURIComponent(projectId)}/${encoded ? `${encoded}/` : ""}`;
}

/** D4：Markdown → Word 导出（后端 Pandoc）。content 传入则优先（未保存内容）。 */
export async function exportDocxFile(
  projectId: string,
  path: string,
  content?: string,
): Promise<Blob> {
  const resp = await fetch("/api/files/export-docx", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId, path, content }),
  });
  if (!resp.ok) {
    let msg = `导出失败（HTTP ${resp.status}）`;
    try {
      const j = await resp.json();
      if (j && typeof j.message === "string") msg = j.message;
    } catch { /* 非 JSON 响应保留默认信息 */ }
    throw new Error(msg);
  }
  return await resp.blob();
}

/** D4：触发浏览器下载 Blob */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function createFileItem(projectId: string, path: string, type: "file" | "directory", content = ""): Promise<FileEntryFull> {
  return request<FileEntryFull>("/api/files/create", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, path, type, content }),
  });
}

export function writeFileContent(projectId: string, path: string, content: string, expectedVersion: string): Promise<{ version: string }> {
  return request<{ version: string }>("/api/files/write", {
    method: "PUT",
    body: JSON.stringify({ project_id: projectId, path, content, expected_version: expectedVersion }),
  });
}

export function renameFileItem(projectId: string, path: string, newPath: string): Promise<unknown> {
  return request<unknown>("/api/files/rename", {
    method: "PUT",
    body: JSON.stringify({ project_id: projectId, path, new_path: newPath }),
  });
}

export function copyFileItem(projectId: string, source: string, destination: string): Promise<unknown> {
  return request<unknown>("/api/files/copy", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, path: source, target_path: destination }),
  });
}

export function deleteFileItem(projectId: string, path: string): Promise<unknown> {
  return request<unknown>("/api/files", {
    method: "DELETE",
    body: JSON.stringify({ project_id: projectId, path }),
  });
}

/** 上传单个文件（multipart）。path=目标目录；relative_path=可选相对子路径（夹内上传用）。 */
export function uploadFile(
  projectId: string,
  path: string,
  file: File,
  relativePath?: string,
  options?: {reuseIfIdentical?: boolean},
): Promise<unknown> {
  const form = new FormData();
  form.append("project_id", projectId);
  form.append("path", path);
  form.append("file", file);
  if (relativePath) form.append("relative_path", relativePath);
  if (options?.reuseIfIdentical) form.append("reuse_if_identical", "1");
  return request<unknown>("/api/files/upload", {
    method: "POST",
    body: form,
    headers: {}, // FormData 自带 multipart 边界，勿覆盖 Content-Type
  });
}
