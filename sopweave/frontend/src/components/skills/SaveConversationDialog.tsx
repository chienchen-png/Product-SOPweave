import { useEffect, useState } from "react";
import { fetchHistory, createSkillDraft, type RunHistory } from "../../lib/api";

export default function SaveConversationDialog({
  projectId,
  onClose,
  onPrepared,
}: {
  projectId: string;
  onClose: () => void;
  onPrepared: (draftId: string) => void;
}) {
  const [runs, setRuns] = useState<RunHistory["runs"]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    fetchHistory(projectId)
      .then((history) => {
        if (!disposed) {
          const rows = history.runs.filter((r) => r.status === "completed");
          setRuns(rows);
          setSelected(rows.map((r) => r.run_id));
        }
      })
      .catch((e) => {
        if (!disposed) setError(e.message);
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [projectId]);
  async function prepare() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const draft = await createSkillDraft({
        project_id: projectId,
        conversation_id: projectId,
        run_ids: selected,
      });
      onPrepared(draft.draft_id);
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
      aria-label="选择对话范围"
      className="dialog-overlay fixed inset-0 z-40 flex items-center justify-center bg-zinc-950/25 p-5 backdrop-blur-[2px]"
    >
      <div className="dialog-content max-h-[80vh] w-full max-w-xl overflow-auto rounded-xl border bg-white p-6">
        <h2 className="font-semibold">选择保存为 SOP 的对话范围</h2>
        <p className="my-2 text-xs text-zinc-500">
          提取选中的已完成任务；下一步预览脱敏来源并修改流程。
        </p>
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
        {runs.map((run) => (
          <label
            key={run.run_id}
            className="my-2 block rounded-lg border p-2 text-sm"
          >
            <input
              type="checkbox"
              disabled={busy}
              checked={selected.includes(run.run_id)}
              onChange={(event) =>
                setSelected((value) =>
                  event.target.checked
                    ? [...value, run.run_id]
                    : value.filter((id) => id !== run.run_id),
                )
              }
            />{" "}
            {run.title}
          </label>
        ))}
        {loading && (
          <p role="status" className="text-sm">
            正在加载可提取的对话…
          </p>
        )}
        {!loading && !error && !runs.length && (
          <p className="text-sm text-zinc-500">暂无可提取的已完成任务。</p>
        )}
        <div className="mt-3 flex gap-3">
          <button
            disabled={busy || loading || !selected.length}
            className="rounded bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-40"
            onClick={() => void prepare()}
          >
            预览来源并准备草稿
          </button>
          <button disabled={busy} onClick={onClose}>
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
