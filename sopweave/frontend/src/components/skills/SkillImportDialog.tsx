import { useState } from "react";
import { previewSkillImport, type SkillImportPreview } from "../../lib/api";

export default function SkillImportDialog({
  onClose,
  onPrepared,
}: {
  onClose: () => void;
  onPrepared: (draftId: string) => void;
}) {
  const [url, setUrl] = useState(""),
    [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState<SkillImportPreview | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function inspect(skill_path = "") {
    setBusy(true);
    setError("");
    try {
      setPreview(
        await previewSkillImport({
          url: preview?.resolved_url || url,
          file: file || undefined,
          skill_path,
        }),
      );
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="导入 Skill"
      className="dialog-overlay fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/25 p-5 backdrop-blur-[2px]"
    >
      <div className="dialog-content max-h-[90vh] w-full max-w-3xl space-y-4 overflow-auto rounded-xl border bg-white p-6">
        <header className="flex justify-between">
          <h2 className="font-semibold">导入 GitHub / ZIP Skill</h2>
          <button disabled={busy} onClick={onClose}>
            关闭
          </button>
        </header>
        <p className="text-sm text-zinc-500">
          检查现有工具与运行环境的兼容性，审阅适配差异后再发布。
        </p>
        <label className="block text-sm">
          公开 GitHub 目录链接
          <input
            aria-label="GitHub Skill 链接"
            className="mt-1 w-full rounded-lg border p-2"
            value={url}
            disabled={busy || !!file}
            onChange={(event) => {
              setUrl(event.target.value);
              setPreview(null);
            }}
            placeholder="https://github.com/组织/仓库/tree/main/skills/目录"
          />
        </label>
        <label className="block text-sm">
          或上传 ZIP
          <input
            aria-label="Skill ZIP 文件"
            type="file"
            accept=".zip"
            disabled={busy}
            onChange={(event) => {
              setFile(event.target.files?.[0] || null);
              setPreview(null);
            }}
          />
        </label>
        <button
          disabled={busy || (!url && !file)}
          className="ui-button ui-button-primary"
          onClick={() => void inspect()}
        >
          {busy ? "检查中…" : "检查兼容性"}
        </button>
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
        {preview?.choices && (
          <div>
            <p className="mb-2 text-sm">仓库中有多个 Skill，请选择：</p>
            {preview.choices.map((path) => (
              <button
                key={path}
                className="m-1 rounded-lg border px-2 py-1 text-sm"
                disabled={busy}
                onClick={() => void inspect(path)}
              >
                {path || "根目录"}
              </button>
            ))}
          </div>
        )}
        {preview?.draft && (
          <>
            <p className="text-sm">
              固定来源：{preview.commit || "ZIP校验和已记录"}；可用工具：
              {preview.supported_tools?.join("、") || "需进一步指定"}
            </p>
            {!!preview.warnings?.length && (
              <ul className="rounded bg-amber-50 p-3 text-sm">
                {preview.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}
            <details open>
              <summary className="cursor-pointer text-sm font-medium">
                原文与项目适配差异
              </summary>
              <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-zinc-50 p-3 text-xs">
                {preview.diff}
              </pre>
            </details>
            <button
              className="rounded bg-blue-600 px-3 py-2 text-sm text-white"
              onClick={() => onPrepared(preview.draft!.draft_id)}
            >
              继续审阅 SOP 草稿
            </button>
          </>
        )}
      </div>
    </div>
  );
}
