/** projects API contracts and requests. */
import { request } from './client';

// =====================================================================
// 项目 /api/projects
// =====================================================================

export interface Project {
  id: string;
  name: string;
  description: string | null;
  work_dir: string;
  year: number | null;
  quarter: number | null;
  default_agent: string | null;
  default_model: string | null;
  work_mode?: "manual" | "auto";
  created_at: string;
  updated_at: string;
}

export function listProjects(includeHidden = false): Promise<Project[]> {
  // includeHidden=true：应用内部（ensureAppProject）显式查找/复用 __app_ 隐藏项目
  return request<Project[]>(`/api/projects${includeHidden ? "?include_hidden=1" : ""}`);
}

export function createProject(payload: {
  name: string;
  description?: string;
  work_dir?: string;
  default_model?: string;
}): Promise<Project> {
  return request<Project>("/api/projects", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateProject(
  projectId: string,
  payload: {
    name?: string;
    description?: string;
    year?: number | null;
    quarter?: number | null;
    default_agent?: string | null;
    default_model?: string | null;
    work_mode?: "manual" | "auto";  // 阶段 G4：工作模式持久化
  },
): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteProject(projectId: string): Promise<unknown> {
  return request<unknown>(`/api/projects/${projectId}`, {
    method: "DELETE",
  });
}

export function duplicateProject(projectId: string): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}/duplicate`, {
    method: "POST",
  });
}
