/** models API contracts and requests. */
import type { ModelOption } from './registry';
import { request } from './client';
import { aiRegistryEvents } from '../catalogEvents';

async function mutate<T>(path: string, options: RequestInit): Promise<T> {
  const result = await request<T>(path, options);
  // The editing page already awaits its refresh; notify the other tabs once.
  aiRegistryEvents.notify(false);
  return result;
}

// =====================================================================
// 模型供应商/模型管理 /api/models
// =====================================================================

export interface Provider {
  id: string;
  name: string;
  api_url: string;
  has_api_key: boolean;
  api_key_url: string;
  description: string | null;
  provider_kind: "deepseek" | "zhipu" | "siliconflow" | "dashscope" | "openai_compatible";
  api_mode: "chat_completions";
  options_json: Record<string, unknown>;
  status: string;
  models?: (ModelRecord & { capability_checks?: CapabilityCheck[] })[];
  icon?: string;
  created_at?: string;
}

export interface ModelRecord extends ModelOption {}

export type ThinkingStyle = "none" | "thinking_object" | "enable_thinking";

export interface ProviderTemplate {
  template_id: "deepseek" | "zhipu" | "siliconflow" | "custom";
  display_name: string;
  provider_kind: "deepseek" | "zhipu" | "siliconflow" | "openai_compatible";
  base_url: string | null;
  api_mode: "chat_completions";
  auth_mode: "bearer";
  thinking_style: ThinkingStyle;
  is_custom: boolean;
}

export interface ProviderBundlePayload {
  template_id: ProviderTemplate["template_id"];
  api_key: string;
  api_model: string;
  provider_name?: string;
  base_url?: string;
  api_mode?: "chat_completions";
  auth_mode?: "bearer";
  thinking_style?: ThinkingStyle;
  context_window?: number;
  timeout?: number;
  max_retries?: number;
}

export interface CapabilityCheck {
  model_id?: string;
  check_name?: string;
  status: string;
  detail?: Record<string, unknown>;
  error_code?: string | null;
}

export interface ProviderBundleResult {
  provider: Provider;
  model: ModelRecord;
  verification: {
    passed: boolean;
    checks: Record<string, CapabilityCheck>;
  };
}

export function listProviders(): Promise<Provider[]> {
  return request<Provider[]>("/api/models/providers");
}

export function listProviderTemplates(): Promise<ProviderTemplate[]> {
  return request<ProviderTemplate[]>("/api/models/provider-templates");
}

export function createProviderBundle(payload: ProviderBundlePayload): Promise<ProviderBundleResult> {
  return mutate<ProviderBundleResult>("/api/models/provider-bundles", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createProvider(payload: Partial<Provider> & { api_key?: string }): Promise<Provider> {
  return mutate<Provider>("/api/models/providers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateProvider(
  providerId: string,
  payload: Partial<Provider> & { api_key?: string; clear_api_key?: boolean },
): Promise<Provider> {
  return mutate<Provider>(`/api/models/providers/${providerId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteProvider(providerId: string): Promise<unknown> {
  return mutate<unknown>(`/api/models/providers/${providerId}`, {
    method: "DELETE",
  });
}

export function createModel(providerId: string, payload: Partial<ModelRecord>): Promise<ModelRecord> {
  return mutate<ModelRecord>(`/api/models/providers/${providerId}/models`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateModel(modelId: string, payload: Partial<ModelRecord>): Promise<ModelRecord> {
  return mutate<ModelRecord>(`/api/models/${modelId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function setDefaultModel(modelId: string): Promise<ModelRecord> {
  return mutate<ModelRecord>(`/api/models/${modelId}/default`, {
    method: "PATCH",
  });
}

export function deleteModel(modelId: string): Promise<unknown> {
  return mutate<unknown>(`/api/models/${modelId}`, {
    method: "DELETE",
  });
}

export function testModelConnection(payload: {
  mode?: "basic" | "agent_contract";
  model_id?: string;
  api_url?: string;
  api_key?: string;
  model?: string;
  provider_kind?: Provider["provider_kind"];
  api_mode?: "chat_completions";
}): Promise<{
  message?: string;
  passed?: boolean;
  checks?: Record<string, CapabilityCheck>;
}> {
  return mutate<{
    message?: string;
    passed?: boolean;
    checks?: Record<string, CapabilityCheck>;
  }>("/api/models/test", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
