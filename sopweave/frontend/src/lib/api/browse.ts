/** browse API contracts and requests. */
import { request } from './client';

// =====================================================================
// 目录树浏览 /api/browse（阶段 7.9.6 方案 B：自研目录选择器）
// =====================================================================

export interface BrowseTreeResult {
  path: string;
  name: string;
  parent: string | null;
  entries: { name: string; path: string; type: "directory" | "file" }[];
}

/** 服务端文件夹选择（阶段 7.9.7.3 恢复）：后端弹 Windows 原生对话框返回绝对路径。
 * 现代 Chrome(120+) 已移除 File.path，webkitdirectory 拿不到绝对路径，必须走服务端。 */
export function browseFolder(): Promise<{ path: string }> {
  return request<{ path: string }>("/api/browse-folder", { method: "POST" });
}

/** 列出磁盘目录的子目录（供目录树浏览备用）。 */
export function browseDirectory(path?: string): Promise<BrowseTreeResult> {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  return request<BrowseTreeResult>(`/api/browse/tree${q}`);
}

/** 驱动器 + 常用位置（阶段 7.9.8：Windows 风格选择器侧栏数据）。 */
export interface BrowseDrivesResult {
  drives: { name: string; path: string; type: string }[];
  locations: { name: string; path: string; type: string }[];
  /** 初始目录（注册表真实桌面，兼容 OneDrive 重定向） */
  start?: string;
}

export function browseDrives(): Promise<BrowseDrivesResult> {
  return request<BrowseDrivesResult>("/api/browse/drives");
}
