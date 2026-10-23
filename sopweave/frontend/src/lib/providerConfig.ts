import type {
  ModelRecord,
  Provider,
  ProviderBundlePayload,
  ProviderTemplate,
  ThinkingStyle,
} from "./api";

export const SUPPORTED_TEMPLATE_IDS = [
  "deepseek",
  "zhipu",
  "siliconflow",
  "custom",
] as const;

export interface ProviderDraft {
  templateId: ProviderTemplate["template_id"];
  providerName: string;
  baseUrl: string;
  apiKey: string;
  apiModel: string;
  apiMode: "chat_completions";
  authMode: "bearer";
  thinkingStyle: ThinkingStyle;
  timeout: number;
  maxRetries: number;
  contextWindow: number;
  isCustom: boolean;
}

export type ProviderDraftErrors = Partial<Record<keyof ProviderDraft, string>>;

/**
 * The registry intentionally exposes only callable models. Configuration
 * cards must instead prefer the provider's own complete model list so users
 * can see a failed contract check and run it again.
 */
export function modelsForProvider(
  provider: Pick<Provider, "id" | "models">,
  availableModels: ModelRecord[],
): ModelRecord[] {
  return provider.models
    ? [...provider.models]
    : availableModels.filter((model) => model.provider_id === provider.id);
}

export function initialProviderDraft(template: ProviderTemplate): ProviderDraft {
  return {
    templateId: template.template_id,
    providerName: template.is_custom ? "" : template.display_name,
    baseUrl: template.base_url ?? "",
    apiKey: "",
    apiModel: "",
    apiMode: "chat_completions",
    authMode: "bearer",
    thinkingStyle: template.thinking_style,
    timeout: 30,
    maxRetries: 0,
    contextWindow: 0,
    isCustom: template.is_custom,
  };
}

export function validateProviderDraft(draft: ProviderDraft): ProviderDraftErrors {
  const errors: ProviderDraftErrors = {};
  if (draft.isCustom && !draft.providerName.trim()) {
    errors.providerName = "请输入供应商名称";
  }
  if (draft.isCustom && !isHttpUrl(draft.baseUrl)) {
    errors.baseUrl = "请输入有效的 HTTP(S) Base URL";
  }
  if (!draft.apiKey.trim()) errors.apiKey = "请输入 API Key";
  if (!draft.apiModel.trim()) errors.apiModel = "请输入 API 模型标识";
  if (draft.timeout <= 0) errors.timeout = "超时必须大于 0 秒";
  if (!Number.isInteger(draft.maxRetries) || draft.maxRetries < 0) {
    errors.maxRetries = "重试次数必须是非负整数";
  }
  if (!Number.isInteger(draft.contextWindow) || draft.contextWindow < 0) {
    errors.contextWindow = "上下文窗口必须是非负整数";
  }
  return errors;
}

export function toProviderBundlePayload(draft: ProviderDraft): ProviderBundlePayload {
  const errors = validateProviderDraft(draft);
  if (Object.keys(errors).length) {
    throw new Error(Object.values(errors)[0]);
  }
  const base: ProviderBundlePayload = {
    template_id: draft.templateId,
    api_key: draft.apiKey.trim(),
    api_model: draft.apiModel.trim(),
    timeout: draft.timeout,
    max_retries: draft.maxRetries,
    context_window: draft.contextWindow,
  };
  if (draft.isCustom) {
    return {
      ...base,
      provider_name: draft.providerName.trim(),
      base_url: draft.baseUrl.trim(),
      api_mode: draft.apiMode,
      auth_mode: draft.authMode,
      thinking_style: draft.thinkingStyle,
    };
  }
  return base;
}

function isHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value.trim());
    return (
      (parsed.protocol === "http:" || parsed.protocol === "https:")
      && Boolean(parsed.hostname)
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash
    );
  } catch {
    return false;
  }
}
