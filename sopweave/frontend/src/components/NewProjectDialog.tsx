/** NewProjectDialog — 新建项目弹窗（阶段 7.8 增强；7.9.8 Windows 风格选择器）。
 *
 * 改动：
 * - 工作目录必填（+「选择文件夹」按钮 → 自研 Windows 11 风格 DirectoryPicker：
 *   左侧此电脑侧栏 + 顶部导航面包屑 + 目录网格，交互对齐系统对话框）
 * - 默认模型下拉（数据源 /api/options，chat 模型）
 * 对接 /api/projects POST（routes/projects.py create_project，已支持 default_model）。
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { FolderOpen, ChevronDown } from "lucide-react";
import { useAiRegistry } from "../context/AiRegistryContext";
import ModelIcon from "./ModelIcon";
import DirectoryPicker from "./DirectoryPicker";
import Dialog from "./ui/Dialog";

export default function NewProjectDialog({
  onCancel,
  onCreate,
}: {
  onCancel: () => void;
  onCreate: (name: string, workDir: string, defaultModel: string) => void | Promise<void>;
}) {
  const fieldId = useId();
  const [name, setName] = useState("");
  const [workDir, setWorkDir] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const { models: registryModels } = useAiRegistry();
  const models = useMemo(() => registryModels.filter((m) => m.type === "chat"), [registryModels]);
  // 阶段 7.9.8：自研 Windows 风格目录选择器开关
  const [picking, setPicking] = useState(false);
  // 默认模型自定义下拉（阶段 7.9.2：显示模型官方 logo）
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const modelMenuRef = useRef<HTMLDivElement>(null);

  const currentModel = models.find((m) => m.id === defaultModel) || null;

  // 点击外部关闭模型下拉
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (modelMenuRef.current && !modelMenuRef.current.contains(e.target as Node)) {
        setModelMenuOpen(false);
      }
    };
    // Dialog content stops bubbling; capture still observes clicks in other fields.
    window.addEventListener("click", handler, true);
    return () => window.removeEventListener("click", handler, true);
  }, []);

  // 注册表变化时保留有效选择；当前模型被删除后切换到新的全局默认。
  useEffect(() => {
    setDefaultModel((current) => {
      if (models.some((m) => m.id === current)) return current;
      return models.find((m) => m.is_default === 1)?.id || models[0]?.id || "";
    });
  }, [models]);

  function handleBrowse() {
    // 阶段 7.9.8：打开自研 Windows 风格目录选择器
    setPicking(true);
  }

  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const canSubmit = name.trim() !== "" && workDir.trim() !== "";

  const submit = async () => {
    if (!canSubmit || creating) return;
    setCreating(true); setError('');
    try { await onCreate(name.trim(), workDir.trim(), defaultModel); }
    catch (cause) { setError((cause as Error).message || '创建失败，请重试'); }
    finally { setCreating(false); }
  };

  return (
    <Dialog title="新建项目" onClose={() => { if (!creating && !picking) onCancel(); }} className="max-w-lg" showClose={false}>
      <div
        className="overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-base font-semibold text-zinc-800">新建项目</h2>
        <label htmlFor={`${fieldId}-name`} className="mb-2 block text-xs font-medium text-zinc-600">项目名称 <span className="text-red-500">*</span></label>
        <input
          id={`${fieldId}-name`}
          aria-required="true"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          placeholder="例如：2026 销售提成核对"
          className="mb-3 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-800 outline-none focus:border-blue-500"
        />
        <label htmlFor={`${fieldId}-directory`} className="mb-2 block text-xs font-medium text-zinc-600">工作目录 <span className="text-red-500">*</span></label>
        <div className="mb-3 flex gap-2">
          <input
            id={`${fieldId}-directory`}
            aria-required="true"
            value={workDir}
            onChange={(e) => setWorkDir(e.target.value)}
            placeholder="选择存放本项目文件的文件夹"
            className="min-w-0 flex-1 rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-800 outline-none focus:border-blue-500"
          />
          <button
            type="button"
            onClick={handleBrowse}
            title="选择文件夹（Windows 风格对话框）"
            className="flex shrink-0 items-center gap-1 rounded-lg border border-zinc-300 px-2.5 text-xs text-zinc-600 hover:bg-zinc-100"
          >
            <FolderOpen size={13} /> 选择文件夹
          </button>
        </div>
        {!workDir.trim() && <p className="-mt-1.5 mb-3 text-xs text-zinc-400">工作目录为必填项，可手动输入或点击「浏览」选择</p>}
        <span id={`${fieldId}-model-label`} className="mb-2 block text-xs font-medium text-zinc-600">默认模型</span>
        <div className="relative mb-4" ref={modelMenuRef}>
          <button
            type="button"
            aria-labelledby={`${fieldId}-model-label ${fieldId}-model-value`}
            aria-expanded={modelMenuOpen}
            onClick={(e) => { e.stopPropagation(); setModelMenuOpen((o) => !o); }}
            className="flex w-full items-center gap-2 rounded-lg border border-zinc-300 px-3 py-2 text-sm text-zinc-800 outline-none focus:border-blue-500"
          >
            {currentModel ? (
              <>
                <ModelIcon name={currentModel.name} icon={currentModel.icon} size={14} />
                <span id={`${fieldId}-model-value`} className="flex-1 truncate text-left">{currentModel.name}</span>
              </>
            ) : (
              <span id={`${fieldId}-model-value`} className="flex-1 text-left text-zinc-400">（无可用模型）</span>
            )}
            <ChevronDown size={13} className="text-zinc-400" />
          </button>
          {modelMenuOpen && (
            <div className="absolute left-0 top-full z-50 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-zinc-200 bg-white py-1 shadow-xl">
              {models.length === 0 && (
                <div className="px-3 py-2 text-xs text-zinc-400">（无可用模型）</div>
              )}
              {models.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setDefaultModel(m.id); setModelMenuOpen(false); }}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-zinc-100 ${
                    m.id === defaultModel ? "bg-blue-50 text-blue-700" : "text-zinc-700"
                  }`}
                >
                  <ModelIcon name={m.name} icon={m.icon} size={14} />
                  <span className="flex-1 truncate">{m.name}</span>
                  {m.is_default === 1 && <span className="rounded bg-blue-50 px-1.5 text-xs text-blue-600">默认</span>}
                </button>
              ))}
            </div>
          )}
        </div>
        {error && <p role="alert" className="mb-3 text-sm text-red-700">{error}</p>}
        <div className="flex justify-end gap-2">
          <button
            onClick={() => { if (!creating) onCancel(); }}
            className="rounded-md px-4 py-2 text-sm text-zinc-600 hover:bg-zinc-100"
          >
            取消
          </button>
          <button
            onClick={() => void submit()}
            disabled={!canSubmit || creating}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-40"
          >
            创建
          </button>
        </div>
      </div>
      {/* 阶段 7.9.8：自研 Windows 11 风格目录选择器 */}
      {picking && (
        <DirectoryPicker
          onSelect={(path) => { setWorkDir(path); setPicking(false); }}
          onCancel={() => setPicking(false)}
        />
      )}
    </Dialog>
  );
}
