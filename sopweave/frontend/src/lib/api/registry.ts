/** registry API contracts and requests. */
import { request } from './client';
import { aiRegistryEvents } from '../catalogEvents';

// =====================================================================
// 模型/Agent 选项 /api/options
// =====================================================================

export interface ModelOption {
  id: string;
  provider_id: string;
  name: string;
  api_model: string;
  type: string;
  capabilities: string;
  capabilities_json: Record<string, boolean>;
  request_options_json: Record<string, unknown>;
  context_window: number;
  is_default: number;
  status: string;
  icon?: string;
}

export interface AgentOption {
  id: string;
  name: string;
  version: string;
  description: string;
  enabled: number | boolean;
}

export interface ToolEntry {
  name: string;
  description: string;
  risk: 'read' | 'write' | 'data_change' | 'command';
  loading: 'default' | 'on_demand';
}

export interface ToolCategory {
  category: string;
  tools: ToolEntry[];
}

export interface AgentDetail {
  id: string;
  name: string;
  version: string;
  enabled: boolean;
  role: string;
  description: string;
  default_model_id: string;
  toolsets: string[];
  tools: string[];
  skills: string[];
  approval_policy: Record<string, "always" | "never" | "work_mode">;
  max_turns: number;
  progressive_tools: boolean;
  allow_user_skills?: boolean;
  system_prompt: string;
  workflow?: Record<string, unknown>;
  context_policy?: Record<string, unknown>;
}

export function fetchAgent(agentId: string): Promise<{ agent: AgentDetail }> {
  return request<{ agent: AgentDetail }>(`/api/agents/${agentId}`);
}

export function fetchAgentTools(agentId: string): Promise<{ agent_id: string; catalog: ToolCategory[] }> {
  return request<{ agent_id: string; catalog: ToolCategory[] }>(`/api/agents/${agentId}/tools`);
}

export async function saveAgentDefinition(
  agentId: string,
  manifest: Record<string, unknown>,
  instructions: string,
): Promise<{ agent: AgentDetail }> {
  const result = await request<{ agent: AgentDetail }>(`/api/agents/${encodeURIComponent(agentId)}`, {
    method: "PUT",
    body: JSON.stringify({ manifest, instructions }),
  });
  aiRegistryEvents.notify(false);
  return result;
}

export interface Options {
  models: ModelOption[];
  agents: AgentOption[];
  agent_definition_errors?: AgentDefinitionError[];
}

export interface AgentDefinitionError {
  agent_id: string;
  path: string;
  message: string;
}

export function fetchOptions(): Promise<Options> {
  return request<Options>("/api/options");
}
