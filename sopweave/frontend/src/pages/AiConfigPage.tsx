import PageHeader from '../components/ui/PageHeader';
import Dialog from "../components/ui/Dialog";
import {useFeedback} from "../components/ui/Feedback";
/** AI 配置：模型供应商、可编辑 Agent 定义，以及后端实时工具清单。 */

import { useCallback, useEffect, useMemo, useState, useId } from "react";
import {
  Plus, Pencil, Trash2, RefreshCw, Server, Cpu, Sparkles, Star, Save, X,
} from "lucide-react";
import {
  updateProvider, deleteProvider,
  createModel, updateModel, setDefaultModel, deleteModel, testModelConnection,
  fetchAgent, fetchAgentTools, saveAgentDefinition,
  type Provider, type ModelRecord, type AgentDetail, type ToolCategory, type AgentDefinitionError,
} from "../lib/api";
import ModelIcon, { MODEL_ICON_LIBRARY, matchModelIcon } from "../components/ModelIcon";
import MarkdownEditor from "../components/MarkdownEditor";
import AgentToolCatalog from "../components/AgentToolCatalog";
import ProviderSetupDialog from "../components/ProviderSetupDialog";
import { useAiRegistry } from "../context/AiRegistryContext";
import { modelsForProvider } from "../lib/providerConfig";

// ---------- 供应商表单 ----------

interface ProviderForm {
  name: string;
  api_url: string;
  api_key: string;
  clear_api_key?: boolean;
  description: string;
  timeout: number;
  max_retries: number;
}

interface ModelForm {
  name: string;
  api_model: string;
  type: string;
  capabilities: string;
  context_window: number;
  is_default: boolean;
  icon: string;
}

const EMPTY_MODEL: ModelForm = {
  name: "", api_model: "", type: "chat", capabilities: "", context_window: 200000,
  is_default: false, icon: "",
};

type SettingsTab = "models" | "agent";

function normalizeSettingsTab(tab?: string): SettingsTab {
  return tab === "agent" ? "agent" : "models";
}

export default function AiConfigPage({tab, onTabChange}: {tab?: string; onTabChange?: (tab: SettingsTab) => void} = {}) {
  const {notify,confirm}=useFeedback();
  const {
    providers,
    providerTemplates,
    models,
    agents,
    agentDefinitionErrors,
    loading,
    error: registryError,
    refresh,
  } = useAiRegistry();
  const [localError, setLocalError] = useState("");
  const error = localError || registryError;
  const modelsByProvider = useMemo(() => {
    const grouped: Record<string, ModelRecord[]> = {};
    for (const provider of providers) {
      grouped[provider.id] = modelsForProvider(provider, models);
    }
    return grouped;
  }, [models, providers]);
  const [activeTab, setActiveTab] = useState<SettingsTab>(() => {
    const tab = new URLSearchParams(window.location.search).get("tab");
    return normalizeSettingsTab(tab || undefined);
  });
  // 智能体配置
  useEffect(() => {
    if (tab) setActiveTab(normalizeSettingsTab(tab));
  }, [tab]);
  const selectTab = (next: SettingsTab) => { setActiveTab(next); onTabChange?.(next); };

  // 智能体配置
  const [agentDetail, setAgentDetail] = useState<AgentDetail | null>(null);
  const [toolCatalog, setToolCatalog] = useState<ToolCategory[]>([]);
  // 弹窗状态
  const [providerDialog, setProviderDialog] = useState<{ editing: Provider; form: ProviderForm } | null>(null);
  const [providerSetupOpen, setProviderSetupOpen] = useState(false);
  const [modelDialog, setModelDialog] = useState<{ providerId: string; editing: ModelRecord | null; form: ModelForm } | null>(null);
  const [verifyingModelId, setVerifyingModelId] = useState("");

  const load = useCallback(async () => {
    setLocalError("");
    try {
      await refresh();
    } catch (cause) {
      setLocalError((cause as Error).message || "AI 配置加载失败");
    }
  }, [refresh]);

  // 加载智能体配置（默认取第一个智能体）
  const loadAgent = useCallback(async (agentId?: string) => {
    try {
      const target = agentId || agents[0]?.id;
      if (!target) { setAgentDetail(null); setToolCatalog([]); return; }
      const [detail, tools] = await Promise.all([
        fetchAgent(target),
        fetchAgentTools(target),
      ]);
      setAgentDetail(detail.agent);
      setToolCatalog(tools.catalog || []);
    } catch (e) {
      setLocalError((e as Error).message);
    }
  }, [agents]);

  // 切到智能体 Tab 时加载数据（懒加载）
  useEffect(() => {
    if (activeTab === "agent") loadAgent();
  }, [activeTab, loadAgent]);

  // ---------- 模型操作：设为默认 ----------

  async function handleSetDefaultModel(m: ModelRecord) {
    try {
      await setDefaultModel(m.id);
      await load();
    } catch (e) {
      notify(`设置默认模型失败: ${(e as Error).message}`);
    }
  }

  // ---------- 供应商操作 ----------

  function openNewProviderDialog() {
    setProviderSetupOpen(true);
  }

  function openEditProviderDialog(provider: Provider) {
    setProviderDialog({
      editing: provider,
      form: {
        name: provider.name,
        api_url: provider.api_url,
        api_key: "",
        description: provider.description || "",
        timeout:
          typeof provider.options_json?.timeout === "number"
            ? provider.options_json.timeout
            : 30,
        max_retries:
          typeof provider.options_json?.max_retries === "number"
            ? provider.options_json.max_retries
            : 0,
      },
    });
  }

  async function handleSaveProvider() {
    if (!providerDialog) return;
    try {
      await updateProvider(providerDialog.editing.id, {
        ...providerDialog.form,
        options_json: {
          ...providerDialog.editing.options_json,
          timeout: providerDialog.form.timeout,
          max_retries: providerDialog.form.max_retries,
        },
      });
      setProviderDialog(null);
      await load();
    } catch (e) {
      notify(`保存供应商失败: ${(e as Error).message}`);
    }
  }

  async function handleDeleteProvider(p: Provider) {
    if (!await confirm(`确定删除供应商「${p.name}」？其下所有模型将一并删除。`)) return;
    try {
      await deleteProvider(p.id);
      await load();
    } catch (e) {
      notify(`删除失败: ${(e as Error).message}`);
    }
  }

  // ---------- 模型操作 ----------

  async function handleSaveModel() {
    if (!modelDialog) return;
    try {
      const form = modelDialog.form;
      const payload = {
        ...form,
        api_model: form.api_model.trim() || form.name.trim(),
        is_default: form.is_default ? 1 : 0,
      };
      if (modelDialog.editing) {
        await updateModel(modelDialog.editing.id, payload);
      } else {
        await createModel(modelDialog.providerId, payload);
      }
      setModelDialog(null);
      await load();
    } catch (e) {
      notify(`保存模型失败: ${(e as Error).message}`);
    }
  }

  async function handleDeleteModel(m: ModelRecord) {
    if (!await confirm(`确定删除模型「${m.name}」？`)) return;
    try {
      await deleteModel(m.id);
      await load();
    } catch (e) {
      notify(`删除失败: ${(e as Error).message}`);
    }
  }

  async function handleVerifyModel(m: ModelRecord) {
    setVerifyingModelId(m.id);
    try {
      const result = await testModelConnection({
        mode: "agent_contract",
        model_id: m.id,
      });
      await load();
      if (!result.passed) {
        notify("模型能力验证未全部通过，请检查 API Key、模型标识与供应商兼容性。");
      }
    } catch (e) {
      notify(`重新验证失败: ${(e as Error).message}`);
    } finally {
      setVerifyingModelId("");
    }
  }

  // ---------- 渲染 ----------

  return (
    <div className="app-page flex min-w-0 flex-1 flex-col">
      {/* 头部 */}
      <PageHeader title="AI 功能配置" description="配置模型供应商、模型与平台智能体"/>

      {/* Tab */}
      <div className="flex gap-1 border-b border-zinc-200 bg-white px-6">
        <TabBtn active={activeTab === "models"} onClick={() => selectTab("models")} icon={<Cpu size={13} />} label="模型配置" />
        <TabBtn active={activeTab === "agent"} onClick={() => selectTab("agent")} icon={<Sparkles size={13} />} label="智能体配置" />
        <div className="ml-auto flex items-center gap-1 pb-2 pt-1.5">
          <button
            className="rounded p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
            onClick={() => void load()}
            title="刷新"
          >
            <RefreshCw size={14} />
          </button>
          {activeTab === "models" && (
            <button
              onClick={openNewProviderDialog}
              title="新增供应商"
              className="rounded p-1.5 text-blue-600 hover:bg-blue-50"
            >
              <Plus size={15} />
            </button>
          )}
        </div>
      </div>

      {/* 内容 */}
      <div className="page-body flex-1 overflow-y-auto">
        {loading && <div className="text-sm text-zinc-400">加载中…</div>}
        {error && <div className="text-sm text-red-500">{error}</div>}

        {!loading && activeTab === "models" && (
          <div className="mx-auto max-w-3xl space-y-2">
            {providers.length === 0 && (
              <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-400">
                暂无供应商，点击右上角「＋」添加
              </div>
            )}
            {providers.map((p) => (
              <div key={p.id} className="rounded-lg border border-zinc-200 bg-white p-4">
                {/* 供应商头部：编辑/删除 */}
                <div className="flex items-center gap-2">
                  <Server size={15} className="text-blue-600" />
                  <span className="text-sm font-medium text-zinc-800">{p.name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs ${p.status === "available" ? "bg-green-50 text-green-700" : "bg-amber-50 text-amber-700"}`}>
                    {p.status === "available" ? "可用" : p.status}
                  </span>
                  <div className="ml-auto flex gap-1">
                    <button className="rounded p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600" title="编辑供应商" onClick={() => openEditProviderDialog(p)}>
                      <Pencil size={13} />
                    </button>
                    <button className="rounded p-1.5 text-zinc-400 hover:bg-red-50 hover:text-red-600" title="删除供应商" onClick={() => handleDeleteProvider(p)}>
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
                <div className="mt-1 truncate font-mono text-xs text-zinc-400">{p.api_url}</div>
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-zinc-500">
                  <span>{providerKindLabel(p.provider_kind)}</span>
                  <span>{p.has_api_key ? "API Key 已配置" : "API Key 未配置"}</span>
                  <span>Chat Completions</span>
                </div>
                {/* 卡片内嵌模型列表（每行可编辑/删除/设默认） */}
                <div className="mt-2 space-y-1 border-t border-zinc-100 pt-2">
                  {(modelsByProvider[p.id] || []).map((m) => (
                    <div key={m.id} className="flex items-center gap-2 rounded px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-50">
                      <ModelIcon name={m.name} icon={m.icon} size={12} />
                      <span className="font-medium text-zinc-700">{m.name}</span>
                      {m.is_default === 1 && <span className="rounded bg-blue-50 px-1.5 text-xs text-blue-700">默认</span>}
                      <span className="text-xs text-zinc-400">
                        {m.capabilities || m.type} ·{" "}
                        {m.context_window > 0
                          ? `${m.context_window.toLocaleString()} ctx`
                          : "窗口未标注"}
                      </span>
                      <span className={`rounded px-1.5 text-xs ${m.status === "available" ? "bg-green-50 text-green-700" : "bg-amber-50 text-amber-700"}`}>
                        {m.status === "available" ? "验证通过" : "未验证"}
                      </span>
                      <div className="ml-auto flex gap-0.5">
                        <button
                          className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-40"
                          title="重新验证模型"
                          disabled={verifyingModelId === m.id}
                          onClick={() => void handleVerifyModel(m)}
                        >
                          <RefreshCw size={12} className={verifyingModelId === m.id ? "animate-spin" : ""} />
                        </button>
                        <button
                          className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
                          title="设为默认模型"
                          disabled={m.is_default === 1}
                          onClick={() => handleSetDefaultModel(m)}
                        >
                          <Star size={12} className={m.is_default === 1 ? "text-blue-600" : ""} />
                        </button>
                        <button
                          className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
                          title="编辑模型"
                          onClick={() => setModelDialog({
                            providerId: p.id,
                            editing: m,
                            form: {
                              name: m.name,
                              api_model: m.api_model || m.name,
                              type: m.type,
                              capabilities: m.capabilities,
                              context_window: m.context_window,
                              is_default: m.is_default === 1,
                              icon: m.icon || "",
                            },
                          })}
                        >
                          <Pencil size={12} />
                        </button>
                        <button className="rounded p-1 text-zinc-400 hover:bg-red-50 hover:text-red-600" title="删除模型" onClick={() => handleDeleteModel(m)}>
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  ))}
                  {(p.models || []).flatMap((model) => model.capability_checks || []).some((check) => check.status !== "passed") && (
                    <div className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
                      能力检测未全部通过：{(p.models || []).flatMap((model) => model.capability_checks || []).filter((check) => check.status !== "passed").map((check) => check.error_code || check.check_name).filter(Boolean).join("、")}
                    </div>
                  )}
                  {(modelsByProvider[p.id] || []).length === 0 && (
                    <div className="text-xs text-zinc-400">该供应商暂无模型，点击下方添加</div>
                  )}
                  <button
                    onClick={() => setModelDialog({ providerId: p.id, editing: null, form: { ...EMPTY_MODEL } })}
                    className="flex items-center gap-1 rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50"
                  >
                    <Plus size={11} /> 添加模型
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {!loading && !error && activeTab === "agent" && (
          <AgentConfigPanel
            detail={agentDetail}
            definitionErrors={agentDefinitionErrors}
            catalog={toolCatalog}
            onSaved={(saved) => { setAgentDetail(saved); void loadAgent(saved.id); void refresh(); }}
          />
        )}

      </div>

      {/* 供应商弹窗 */}
      {providerSetupOpen && (
        <ProviderSetupDialog
          templates={providerTemplates}
          onClose={() => setProviderSetupOpen(false)}
          onCreated={async () => { await load(); }}
        />
      )}
      {providerDialog && (
        <Dialog title="编辑供应商" onClose={()=>setProviderDialog(null)} className="max-w-md">
          <div className="overflow-y-auto p-6 pt-12">
            <h3 className="mb-4 text-sm font-semibold text-zinc-800">编辑供应商</h3>
            <FormField label="名称" value={providerDialog.form.name} readOnly={providerDialog.editing.provider_kind !== "openai_compatible"} onChange={(v) => setProviderDialog({ ...providerDialog, form: { ...providerDialog.form, name: v } })} />
            <FormField label="API 地址" value={providerDialog.form.api_url} readOnly={providerDialog.editing.provider_kind !== "openai_compatible"} onChange={(v) => setProviderDialog({ ...providerDialog, form: { ...providerDialog.form, api_url: v } })} />
            <FormField label="API Key" value={providerDialog.form.api_key} onChange={(v) => setProviderDialog({ ...providerDialog, form: { ...providerDialog.form, api_key: v, clear_api_key: false } })} placeholder={providerDialog.editing.has_api_key ? "留空则保留现有密钥" : "sk-..."} type="password" />
            <label className="mb-3 flex items-center gap-2 text-xs text-zinc-600">
              <input type="checkbox" checked={Boolean(providerDialog.form.clear_api_key)} onChange={(event) => setProviderDialog({ ...providerDialog, form: { ...providerDialog.form, api_key: "", clear_api_key: event.target.checked } })} />
              清除已保存的 API Key
            </label>
            <FormField label="描述（可选）" value={providerDialog.form.description} onChange={(v) => setProviderDialog({ ...providerDialog, form: { ...providerDialog.form, description: v } })} placeholder="备注" />
            <FormField label="请求超时（秒）" value={String(providerDialog.form.timeout)} onChange={(v) => setProviderDialog({ ...providerDialog, form: { ...providerDialog.form, timeout: Math.max(1, Number(v) || 1) } })} />
            <FormField label="临时错误重试次数" value={String(providerDialog.form.max_retries)} onChange={(v) => setProviderDialog({ ...providerDialog, form: { ...providerDialog.form, max_retries: Math.max(0, Number(v) || 0) } })} />
            <div className="mt-4 flex justify-end gap-2">
              <button className="rounded-md px-4 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100" onClick={() => setProviderDialog(null)}>取消</button>
              <button
                onClick={handleSaveProvider}
                disabled={!providerDialog.form.name || !providerDialog.form.api_url}
                className="rounded-md bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-500 disabled:opacity-40"
              >
                保存
              </button>
            </div>
          </div>
        </Dialog>
      )}

      {/* 模型弹窗 */}
      {modelDialog && (
        <Dialog title={modelDialog.editing?"编辑模型":"添加模型"} onClose={()=>setModelDialog(null)} className="max-w-md">
          <div className="overflow-y-auto p-6 pt-12">
            <h3 className="mb-4 text-sm font-semibold text-zinc-800">
              {modelDialog.editing ? "编辑模型" : "添加模型"}
            </h3>
            <FormField label="模型名称" value={modelDialog.form.name} onChange={(v) => setModelDialog({ ...modelDialog, form: { ...modelDialog.form, name: v } })} placeholder="如 deepseek-chat / glm-4" />
            <FormField label="API 模型标识" value={modelDialog.form.api_model} onChange={(v) => setModelDialog({ ...modelDialog, form: { ...modelDialog.form, api_model: v } })} placeholder="如 deepseek-chat（修改显示名称不会改变此值）" />
            <label className="mb-1 block text-xs font-medium text-zinc-600">图标</label>
            {/* 图标网格选择器——输入名称自动匹配高亮，点选覆盖，选中变蓝即表达状态 */}
            <div className="mb-3">
              <div className="flex flex-wrap gap-1.5">
                {MODEL_ICON_LIBRARY.map((ic) => {
                  const autoMatched = !modelDialog.form.icon && matchModelIcon(modelDialog.form.name) === ic.file;
                  const selected = modelDialog.form.icon === ic.file;
                  return (
                    <button
                      key={ic.file}
                      type="button"
                      title={ic.label}
                      onClick={(e) => {
                        e.stopPropagation();
                        setModelDialog({
                          ...modelDialog,
                          form: {
                            ...modelDialog.form,
                            icon: selected ? "" : ic.file, // 再点一次取消（回退自动）
                          },
                        });
                      }}
                      className={`flex items-center gap-1 rounded-lg border px-1.5 py-1 transition-colors ${
                        selected
                          ? "border-blue-500 bg-blue-50"
                          : autoMatched
                            ? "border-blue-300 bg-blue-50/50 hover:bg-blue-50"
                            : "border-zinc-200 hover:bg-zinc-50"
                      }`}
                    >
                      <ModelIcon name={ic.label} icon={ic.file} size={16} />
                      <span className={`text-xs ${selected ? "text-blue-700" : autoMatched ? "text-blue-600" : "text-zinc-500"}`}>
                        {ic.label}
                      </span>
                      {autoMatched && !selected && (
                        <span className="rounded bg-blue-600 px-1 text-xs text-white">自动</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
            <label className="mb-1 block text-xs font-medium text-zinc-600">类型</label>
            <select
              value={modelDialog.form.type}
              onChange={(e) => setModelDialog({ ...modelDialog, form: { ...modelDialog.form, type: e.target.value } })}
              className="mb-3 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-800 outline-none focus:border-blue-500"
            >
              <option value="chat">chat（对话）</option>
              <option value="embedding">embedding（嵌入）</option>
              <option value="reranker">reranker（排序）</option>
            </select>
            <FormField label="能力（逗号分隔）" value={modelDialog.form.capabilities} onChange={(v) => setModelDialog({ ...modelDialog, form: { ...modelDialog.form, capabilities: v } })} placeholder="对话,工具" />
            <FormField label="上下文窗口" value={String(modelDialog.form.context_window)} onChange={(v) => setModelDialog({ ...modelDialog, form: { ...modelDialog.form, context_window: Number(v) || 0 } })} placeholder="200000" />
            <label className="mb-3 flex items-center gap-2 text-sm text-zinc-700">
              <input
                type="checkbox"
                checked={modelDialog.form.is_default}
                onChange={(e) => setModelDialog({ ...modelDialog, form: { ...modelDialog.form, is_default: e.target.checked } })}
                className="h-3.5 w-3.5 rounded-lg border-zinc-300"
              />
              设为默认模型
            </label>
            <div className="flex justify-end gap-2">
              <button className="rounded-md px-4 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100" onClick={() => setModelDialog(null)}>取消</button>
              <button
                onClick={handleSaveModel}
                disabled={!modelDialog.form.name}
                className="rounded-md bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-500 disabled:opacity-40"
              >
                保存
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}

// ---------- 子组件 ----------

function providerKindLabel(kind: Provider["provider_kind"]): string {
  return {
    deepseek: "DeepSeek 官方",
    zhipu: "智谱 GLM 官方",
    siliconflow: "硅基流动",
    dashscope: "历史 DashScope",
    openai_compatible: "自定义供应商",
  }[kind];
}

function TabBtn({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 border-b-2 px-3 pb-2 pt-1 text-sm transition-colors ${
        active ? "border-blue-600 text-blue-700" : "border-transparent text-zinc-500 hover:text-zinc-700"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function FormField({ label, value, onChange, placeholder, type, readOnly = false }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string; readOnly?: boolean;
}) {
  const id=useId();
  return (
    <>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-zinc-600">{label}</label>
      <input id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        readOnly={readOnly}
        type={type || "text"}
        placeholder={placeholder}
        autoComplete={type === "password" ? "new-password" : undefined}
        className={`mb-3 w-full rounded-lg border px-3 py-2 text-sm outline-none ${readOnly ? "border-zinc-200 bg-zinc-50 text-zinc-500" : "border-zinc-300 text-zinc-800 focus:border-blue-500"}`}
      />
    </>
  );
}

// ---------- 智能体定义与运行能力 ----------

export function AgentConfigPanel({
  detail,
  catalog,
  onSaved,
  definitionErrors = [],
}: {
  detail: AgentDetail | null;
  catalog: ToolCategory[];
  onSaved: (detail: AgentDetail) => void;
  definitionErrors?: AgentDefinitionError[];
}) {
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [name, setName] = useState("");
  const [modelId, setModelId] = useState("");
  const [toolsets, setToolsets] = useState("");
  const [skills, setSkills] = useState("");
  const [instructions, setInstructions] = useState("");
  const diagnostics = definitionErrors.length > 0 && (
    <div role="alert" className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
      <p className="font-medium">以下智能体定义已停用，其他有效智能体仍可使用。</p>
      <ul className="mt-2 space-y-2">
        {definitionErrors.map(issue => <li key={issue.path}><span className="font-mono">{issue.path}</span><p className="mt-1 break-words">{issue.message}</p></li>)}
      </ul>
    </div>
  );
  if (!detail) {
    return <>{diagnostics}<div className="text-sm text-zinc-400">{definitionErrors.length ? "没有可用智能体，请修正下列定义文件后刷新。" : "加载智能体配置中…"}</div></>;
  }
  const beginEdit = () => {
    setName(detail.name);
    setModelId(detail.default_model_id || "");
    setToolsets((detail.toolsets || []).join(", "));
    setSkills((detail.skills || []).join(", "));
    setInstructions(detail.system_prompt || "");
    setSaveError("");
    setEditing(true);
  };
  const save = async () => {
    setSaving(true);
    setSaveError("");
    const split = (value: string) => value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
    try {
      const result = await saveAgentDefinition(detail.id, {
        id: detail.id,
        name,
        version: detail.version,
        description: detail.description,
        enabled: detail.enabled,
        default_model_id: modelId,
        toolsets: split(toolsets),
        tools: detail.tools || [],
        skills: split(skills),
        approval_policy: detail.approval_policy || {},
        max_turns: detail.max_turns,
        progressive_tools: detail.progressive_tools,
        allow_user_skills: detail.allow_user_skills ?? false,
      }, instructions);
      onSaved(result.agent);
      setEditing(false);
    } catch (caught) {
      setSaveError((caught as Error).message);
    } finally {
      setSaving(false);
    }
  };
  if (editing) {
    return (
      <div className="mx-auto max-w-4xl rounded-xl border border-zinc-200 bg-white p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-zinc-800">编辑 {detail.id}</h2>
            <p className="mt-0.5 text-xs text-zinc-400">保存前校验当前智能体的定义、工具和 Skill 引用</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => setEditing(false)} className="flex items-center gap-1 rounded-md px-3 py-1.5 text-xs text-zinc-500 hover:bg-zinc-100">
              <X size={13} /> 取消
            </button>
            <button disabled={saving} onClick={() => void save()} className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-50">
              <Save size={13} /> {saving ? "校验中…" : "校验并保存"}
            </button>
          </div>
        </div>
        {saveError && <div className="mb-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-600">{saveError}</div>}
        <div className="grid grid-cols-2 gap-x-4">
          <FormField label="显示名称" value={name} onChange={setName} />
          <FormField label="默认模型 ID" value={modelId} onChange={setModelId} placeholder="留空则使用项目所选模型" />
          <FormField label="Toolsets（逗号分隔）" value={toolsets} onChange={setToolsets} />
          <FormField label="Skills（逗号分隔）" value={skills} onChange={setSkills} />
        </div>
        <label className="mb-2 block text-xs font-medium text-zinc-600">instructions.md</label>
        <MarkdownEditor
          key={`agent-editor-${detail.id}`}
          initialValue={instructions}
          onChange={setInstructions}
          height={520}
          showExport={false}
        />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      {diagnostics}
      {/* 概览 */}
      <div className="rounded-lg border border-zinc-200 bg-white p-4">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-blue-600" />
          <span className="text-sm font-semibold text-zinc-800">{detail.name || "通用智能体"}</span>
          <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700">v{detail.version || "1.0.0"}</span>
          <span className="ml-auto rounded-full bg-green-50 px-2 py-0.5 text-xs text-green-700">启用</span>
          <button onClick={beginEdit} className="flex items-center gap-1 rounded-lg border border-zinc-200 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-50">
            <Pencil size={11} /> 编辑定义
          </button>
        </div>
        {detail.description && <p className="mt-2 text-sm leading-relaxed text-zinc-500">{detail.description}</p>}
      </div>

      <div className="rounded-lg border border-zinc-200 bg-white p-4">
        <div className="mb-2 text-sm font-semibold text-zinc-700">运行策略</div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs text-zinc-600">
          <InfoRow label="运行框架" value="OpenAI Agents SDK" />
          <InfoRow label="最大模型轮次" value={String(detail.max_turns)} />
          <InfoRow label="工具加载" value={detail.progressive_tools ? "按需加载" : "全部授权工具"} />
          <InfoRow label="授权 Skill" value={`${detail.skills.length} 个`} />
        </div>
        <p className="mt-2 text-xs text-zinc-500">手动模式下写操作需审批，本地程序执行始终需审批；业务信息缺失时通过问题表单补充。</p>
      </div>
      <AgentToolCatalog catalog={catalog} />
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-zinc-50 py-1">
      <span className="text-zinc-400">{label}</span>
      <span className="font-medium text-zinc-700">{value}</span>
    </div>
  );
}
