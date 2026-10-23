import { useMemo, useState } from "react";
import Dialog from "./ui/Dialog";
import { CheckCircle2, ChevronLeft, Loader2, Server, X } from "lucide-react";

import {
  createProviderBundle,
  type ProviderBundleResult,
  type ProviderTemplate,
} from "../lib/api";
import {
  initialProviderDraft,
  toProviderBundlePayload,
  validateProviderDraft,
  type ProviderDraft,
} from "../lib/providerConfig";

interface ProviderSetupDialogProps {
  templates: ProviderTemplate[];
  onClose: () => void;
  onCreated: (result: ProviderBundleResult) => Promise<void> | void;
}

export default function ProviderSetupDialog({
  templates,
  onClose,
  onCreated,
}: ProviderSetupDialogProps) {
  const [draft, setDraft] = useState<ProviderDraft | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const errors = useMemo(
    () => (draft ? validateProviderDraft(draft) : {}),
    [draft],
  );

  function close() {
    if (submitting) return;
    setDraft(null);
    setError("");
    onClose();
  }

  async function submit() {
    if (!draft || Object.keys(errors).length) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await createProviderBundle(toProviderBundlePayload(draft));
      setDraft(null);
      await onCreated(result);
      onClose();
    } catch (cause) {
      setError((cause as Error).message || "供应商配置失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog title="配置供应商" onClose={close} className="max-w-xl" showClose={false}>
      <div className="overflow-y-auto bg-white p-6" onClick={(event) => event.stopPropagation()}>
        <div className="mb-4 flex items-center gap-2">
          {draft && (
            <button className="rounded p-1 text-zinc-500 hover:bg-zinc-100" onClick={() => { setDraft(null); setError(""); }} title="返回模板选择">
              <ChevronLeft size={16} />
            </button>
          )}
          <h3 className="text-sm font-semibold text-zinc-800">
            {draft ? "配置供应商" : "选择供应商"}
          </h3>
          <button className="ml-auto rounded p-1 text-zinc-400 hover:bg-zinc-100" onClick={close} title="关闭">
            <X size={16} />
          </button>
        </div>

        {!draft ? (
          <div className="grid grid-cols-2 gap-3">
            {templates.map((template) => (
              <button
                key={template.template_id}
                className="rounded-lg border border-zinc-200 p-4 text-left hover:border-blue-400 hover:bg-blue-50/40"
                onClick={() => setDraft(initialProviderDraft(template))}
              >
                <Server size={18} className="mb-2 text-blue-600" />
                <div className="text-sm font-semibold text-zinc-800">{template.display_name}</div>
                <div className="mt-1 break-all text-xs text-zinc-400">
                  {template.base_url || "OpenAI 兼容服务"}
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div>
            <div className="mb-4 rounded-lg bg-zinc-50 px-3 py-2 text-xs text-zinc-500">
              连接检测会验证文本、流式、工具调用、工具续接和 usage。全部通过后模型才会进入项目与集成应用的模型列表。
            </div>

            <Field label="供应商名称" value={draft.providerName} readOnly={!draft.isCustom} error={errors.providerName} onChange={(providerName) => setDraft({ ...draft, providerName })} />
            <Field label="Base URL" value={draft.baseUrl} readOnly={!draft.isCustom} error={errors.baseUrl} onChange={(baseUrl) => setDraft({ ...draft, baseUrl })} placeholder="https://provider.example/v1" />
            <Field label="API Key" value={draft.apiKey} type="password" error={errors.apiKey} onChange={(apiKey) => setDraft({ ...draft, apiKey })} placeholder="sk-..." />
            <Field label="API 模型标识" value={draft.apiModel} error={errors.apiModel} onChange={(apiModel) => setDraft({ ...draft, apiModel })} placeholder="从供应商控制台复制" />

            <label className="mb-1 block text-xs font-medium text-zinc-600">API 协议</label>
            <select value={draft.apiMode} disabled className="mb-3 w-full rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-600">
              <option value="chat_completions">OpenAI Chat Completions</option>
            </select>
            <div className="mb-3 text-xs text-zinc-500">鉴权方式：Authorization Bearer API Key</div>

            <button className="mb-3 text-xs text-blue-600 hover:text-blue-700" onClick={() => setAdvanced((value) => !value)}>
              {advanced ? "收起高级设置" : "展开高级设置"}
            </button>
            {advanced && (
              <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3">
                {draft.isCustom && (
                  <>
                    <label className="mb-1 block text-xs font-medium text-zinc-600">思考参数兼容</label>
                    <select
                      value={draft.thinkingStyle}
                      onChange={(event) => setDraft({ ...draft, thinkingStyle: event.target.value as ProviderDraft["thinkingStyle"] })}
                      className="mb-3 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm"
                    >
                      <option value="none">不发送扩展参数</option>
                      <option value="thinking_object">thinking 对象</option>
                      <option value="enable_thinking">enable_thinking</option>
                    </select>
                  </>
                )}
                <NumberField label="超时（秒）" value={draft.timeout} error={errors.timeout} min={1} onChange={(timeout) => setDraft({ ...draft, timeout })} />
                <NumberField label="重试次数" value={draft.maxRetries} error={errors.maxRetries} min={0} onChange={(maxRetries) => setDraft({ ...draft, maxRetries })} />
                <NumberField label="上下文窗口" value={draft.contextWindow} error={errors.contextWindow} min={0} onChange={(contextWindow) => setDraft({ ...draft, contextWindow })} />
              </div>
            )}

            {error && <div className="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
            <div className="mt-4 flex justify-end gap-2">
              <button className="rounded-md px-4 py-2 text-sm text-zinc-600 hover:bg-zinc-100" onClick={close}>取消</button>
              <button
                className="flex items-center gap-1.5 rounded-md bg-blue-600 px-4 py-2 text-sm text-white disabled:cursor-not-allowed disabled:opacity-50"
                disabled={submitting || Object.keys(errors).length > 0}
                onClick={() => void submit()}
              >
                {submitting ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                测试并保存
              </button>
            </div>
          </div>
        )}
      </div>
    </Dialog>
  );
}

function Field({ label, value, onChange, placeholder, type = "text", readOnly = false, error }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  readOnly?: boolean;
  error?: string;
}) {
  return (
    <label className="mb-3 block">
      <span className="mb-1 block text-xs font-medium text-zinc-600">{label}</span>
      <input
        value={value}
        type={type}
        readOnly={readOnly}
        autoComplete={type === "password" ? "new-password" : undefined}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className={`w-full rounded-lg border px-3 py-2 text-sm outline-none ${readOnly ? "border-zinc-200 bg-zinc-50 text-zinc-500" : "border-zinc-300 focus:border-blue-500"}`}
      />
      {error && <span className="mt-1 block text-xs text-red-600">{error}</span>}
    </label>
  );
}

function NumberField({ label, value, onChange, min, error }: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min: number;
  error?: string;
}) {
  return (
    <label className="mb-3 block last:mb-0">
      <span className="mb-1 block text-xs font-medium text-zinc-600">{label}</span>
      <input
        type="number"
        min={min}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-blue-500"
      />
      {error && <span className="mt-1 block text-xs text-red-600">{error}</span>}
    </label>
  );
}
