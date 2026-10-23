/** skills API contracts and requests. */
import { request } from "./client";

// One shared contract for builtin definitions, user revisions and review drafts.

export interface SkillSummary {
  name: string;
  description: string;
  trigger_words: string[];
  step_count: number;
  path: string;
  title?: string;
  origin?: "builtin" | "user";
  readonly?: boolean;
  enabled?: boolean;
  version?: number | string;
  content_digest?: string;
}

export interface SkillBranch {
  key: string;
  title: string;
  summary: string;
}

export interface SkillStep {
  n: number;
  title: string;
  summary: string;
  branch_point: boolean;
  branches: SkillBranch[];
}

export interface SkillModelCategory {
  category: string;
  name: string;
  count: number;
  items: string[];
}

export interface SkillCapabilities {
  models: SkillModelCategory[];
  stats: string[];
  charts: string[];
}

export interface SkillDetail {
  name: string;
  description: string;
  user_invocable: boolean;
  trigger_words: string[];
  purpose: string;
  steps: SkillStep[];
  constraints: string;
  capabilities: SkillCapabilities;
  content: string;
  body: string;
  toolsets: string[];
  origin?: "builtin" | "user";
  readonly?: boolean;
  enabled?: boolean;
  title?: string;
  version?: number | string;
  required_tools?: string[];
  inputs?: SkillInput[];
  outputs?: string[];
  execution_policy?: ExecutionPolicy;
  advisory_rules?: string[];
  spec?: SkillSpec;
}

export interface SkillPage {
  skills: SkillSummary[];
  generation?: number;
  next_cursor?: string;
  user_skills_enabled?: boolean;
}
export function fetchSkills(
  options: {
    query?: string;
    cursor?: string;
    includeInternal?: boolean;
  } = {},
): Promise<SkillPage> {
  const params = new URLSearchParams();
  if (options.query) params.set("query", options.query);
  if (options.cursor) params.set("cursor", options.cursor);
  if (options.includeInternal) params.set("include_internal", "true");
  return request<SkillPage>(`/api/skills?${params.toString()}`);
}

export function fetchSkillDetail(name: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`);
}

export interface SkillInput {
  name: string;
  description: string;
  required: boolean;
  default: string;
}
export interface SopSelection {
  mode: "auto" | "none" | "explicit";
  skill_id: string;
  inputs?: Record<string, string>;
}
export interface ExecutionPolicy {
  currency: string;
  decimal_places: number;
  tolerance: string;
  required_inputs: string[];
  require_approval_for: string[];
  write_paths: string[];
  source_versions: { path: string; version: string }[];
  amount_fields: string[];
  checks: string[];
}
export interface SkillSpec {
  schema_version: 1;
  title: string;
  description: string;
  inputs: SkillInput[];
  outputs: string[];
  required_tools: string[];
  steps: {
    id: string;
    title: string;
    instruction: string;
    tool_ids: string[];
  }[];
  execution_policy: ExecutionPolicy;
  advisory_rules: string[];
  blockers: string[];
}
export interface SkillDraft {
  import_diff?: string;
  draft_id: string;
  draft_revision: number;
  state: string;
  spec: SkillSpec | null;
  source: {
    kind: string;
    display_name?: string;
    text?: string;
    locations?: Record<string, unknown>[];
    warnings?: string[];
  }[];
  questions: {
    question_id: string;
    prompt: string;
    required: boolean;
    options: string[];
  }[];
  answers: { question_id: string; value: string }[];
  generated_digest: string;
  preview?: SkillDetail;
  job_id?: string;
  job_status?: { state?: string; message?: string };
}

export interface SkillImportPreview {
  choices?: string[];
  resolved_url?: string;
  draft?: SkillDraft;
  original_content?: string;
  diff?: string;
  unsupported_tools?: string[];
  supported_tools?: string[];
  warnings?: string[];
  commit?: string;
  requires_adaptation?: boolean;
}
export async function previewSkillImport(options: {
  url?: string;
  file?: File;
  skill_path?: string;
}): Promise<SkillImportPreview> {
  if (options.file) {
    const body = new FormData();
    body.append("file", options.file);
    if (options.skill_path) body.append("skill_path", options.skill_path);
    return request("/api/skills/imports/preview", {
      method: "POST",
      headers: {},
      body,
    });
  }
  return request("/api/skills/imports/preview", {
    method: "POST",
    body: JSON.stringify({
      url: options.url,
      skill_path: options.skill_path || "",
    }),
  });
}
export interface SkillRef {
  skill_id: string;
  origin: "builtin" | "user";
  version: number | string;
  content_digest: string;
}
export interface DraftEvent {
  type: string;
  job_id: string;
  draft_revision: number;
  data: Record<string, unknown>;
}
const draftPath = (id: string) =>
  `/api/skills/drafts/${encodeURIComponent(id)}`;
export const createSkillDraft = (body: Record<string, unknown> = {}) =>
  request<SkillDraft>("/api/skills/drafts", {
    method: "POST",
    body: JSON.stringify(body),
  });
export const fetchSkillDraft = (id: string) =>
  request<SkillDraft>(draftPath(id));
export interface SkillDraftSummary {
  draft_id: string;
  draft_revision: number;
  title: string;
  state: string;
  updated_at: string;
}
export const listSkillDrafts = (cursor = '') => request<{drafts:SkillDraftSummary[];next_cursor:string}>(
  `/api/skills/drafts?${new URLSearchParams(cursor ? {cursor} : {})}`,
);
export const updateSkillDraft = (id: string, body: Record<string, unknown>) =>
  request<SkillDraft>(draftPath(id), {
    method: "PATCH",
    body: JSON.stringify(body),
  });
export const publishSkillDraft = (draft: SkillDraft, key: string) =>
  request<{ ref: SkillRef; generation: number }>(
    draftPath(draft.draft_id) + "/publish",
    {
      method: "POST",
      body: JSON.stringify({
        expected_revision: draft.draft_revision,
        confirmed_digest: draft.generated_digest,
        idempotency_key: key,
      }),
    },
  );
export const deleteUserSkill = (id: string, version: number | string) =>
  request<{ deleted: boolean; generation: number }>(
    `/api/skills/${encodeURIComponent(id)}`,
    { method: "DELETE", body: JSON.stringify({ expected_version: version }) },
  );
export const cancelSkillGeneration = (id: string) =>
  request<SkillDraft>(draftPath(id) + "/cancel", { method: "POST" });
export async function uploadSkillSource(
  id: string,
  file: File,
): Promise<SkillDraft> {
  const body = new FormData();
  body.append("file", file);
  return request<SkillDraft>(draftPath(id) + "/sources", {
    method: "POST",
    headers: {},
    body,
  });
}
export async function generateSkillDraft(
  draft: SkillDraft,
  model_id: string,
  onEvent: (event: DraftEvent) => void,
  signal?: AbortSignal,
) {
  const response = await fetch(draftPath(draft.draft_id) + "/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_revision: draft.draft_revision, model_id }),
    signal,
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.message || "创作请求失败");
  }
  if (!response.body) throw new Error("创作事件流不可用");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(
        /\r\n/g,
        "\n",
      );
      let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const packet = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const raw = packet
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart())
          .join("\n");
        if (raw) {
          const event = JSON.parse(raw) as DraftEvent;
          onEvent(event);
          if (event.type === "error")
            throw new Error(String(event.data.message));
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
