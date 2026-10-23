/** workspace API contracts and requests. */
import { request } from "./client";
import type { SkillRef } from "./skills";

// 历史恢复
export interface RunHistory {
  runs: {
    run_id: string;
    last_seq?:number;
    sop_ref?: SkillRef | null;
    title: string;
    status: string;
    error_message?: string;
    user_prompt: string;
    assistant_text: string;
    reasoning: string | null;
    context_omitted_items?: number;
    tool_cards: unknown[];
    plan: unknown;
    created_at: string;
  }[];
}

export function fetchHistory(projectId: string): Promise<RunHistory> {
  return request<RunHistory>(
    `/api/workspace/history?project_id=${encodeURIComponent(projectId)}`,
  );
}
