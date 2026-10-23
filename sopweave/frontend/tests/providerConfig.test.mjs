import test from "node:test";
import assert from "node:assert/strict";
import {
  SUPPORTED_TEMPLATE_IDS,
  initialProviderDraft,
  modelsForProvider,
  toProviderBundlePayload,
  validateProviderDraft,
} from "../.test-build/providerConfig.js";

const templates = {
  deepseek: template("deepseek", "deepseek", "https://api.deepseek.com", "thinking_object", false),
  zhipu: template("zhipu", "zhipu", "https://open.bigmodel.cn/api/paas/v4", "thinking_object", false),
  siliconflow: template("siliconflow", "siliconflow", "https://api.siliconflow.cn/v1", "enable_thinking", false),
  custom: template("custom", "openai_compatible", null, "none", true),
};

test("setup exposes exactly the four approved template cards", () => {
  assert.deepEqual(SUPPORTED_TEMPLATE_IDS, ["deepseek", "zhipu", "siliconflow", "custom"]);
});

test("official drafts lock name URL protocol auth and thinking style", () => {
  const draft = initialProviderDraft(templates.deepseek);

  assert.equal(draft.providerName, "deepseek 显示名");
  assert.equal(draft.baseUrl, "https://api.deepseek.com");
  assert.equal(draft.apiMode, "chat_completions");
  assert.equal(draft.authMode, "bearer");
  assert.equal(draft.thinkingStyle, "thinking_object");
  assert.equal(draft.isCustom, false);
  assert.equal(draft.apiKey, "");
  assert.equal("responses" in draft, false);
  assert.equal("optionsJson" in draft, false);
});

test("official drafts require only API Key and API model", () => {
  const empty = initialProviderDraft(templates.zhipu);
  assert.deepEqual(validateProviderDraft(empty), {
    apiKey: "请输入 API Key",
    apiModel: "请输入 API 模型标识",
  });

  const complete = { ...empty, apiKey: "secret", apiModel: "glm-4.7-flash" };
  assert.deepEqual(validateProviderDraft(complete), {});
});

test("custom draft requires controlled fields and rejects invalid URL", () => {
  const draft = initialProviderDraft(templates.custom);
  assert.deepEqual(validateProviderDraft(draft), {
    providerName: "请输入供应商名称",
    baseUrl: "请输入有效的 HTTP(S) Base URL",
    apiKey: "请输入 API Key",
    apiModel: "请输入 API 模型标识",
  });

  const invalid = {
    ...draft,
    providerName: "Proxy",
    baseUrl: "ftp://proxy.example/v1",
    apiKey: "secret",
    apiModel: "vendor-model",
  };
  assert.equal(validateProviderDraft(invalid).baseUrl, "请输入有效的 HTTP(S) Base URL");
});

test("payload conversion sends only controlled provider fields", () => {
  const draft = {
    ...initialProviderDraft(templates.custom),
    providerName: "Proxy",
    baseUrl: "https://proxy.example/v1/chat/completions",
    apiKey: "secret",
    apiModel: "vendor/model",
    thinkingStyle: "enable_thinking",
    timeout: 45,
    maxRetries: 1,
    contextWindow: 128000,
  };

  assert.deepEqual(toProviderBundlePayload(draft), {
    template_id: "custom",
    api_key: "secret",
    api_model: "vendor/model",
    provider_name: "Proxy",
    base_url: "https://proxy.example/v1/chat/completions",
    api_mode: "chat_completions",
    auth_mode: "bearer",
    thinking_style: "enable_thinking",
    timeout: 45,
    max_retries: 1,
    context_window: 128000,
  });
});

test("configuration cards include unverified provider models", () => {
  const provider = {
    id: "p1",
    models: [{ id: "unverified", provider_id: "p1", status: "unverified" }],
  };
  const available = [{ id: "available", provider_id: "p1", status: "available" }];

  assert.deepEqual(modelsForProvider(provider, available).map((model) => model.id), ["unverified"]);
});

function template(templateId, providerKind, baseUrl, thinkingStyle, isCustom) {
  return {
    template_id: templateId,
    display_name: `${templateId} 显示名`,
    provider_kind: providerKind,
    base_url: baseUrl,
    api_mode: "chat_completions",
    auth_mode: "bearer",
    thinking_style: thinkingStyle,
    is_custom: isCustom,
  };
}
