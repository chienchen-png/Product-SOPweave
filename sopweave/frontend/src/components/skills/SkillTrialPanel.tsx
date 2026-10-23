import { useState } from "react";
import { request } from "../../lib/api";
import AgentInteractionPanel from "../AgentInteraction";
import type { AgentInteraction } from "../../lib/agentInteractions";
import type { AgentResumeCommand } from "../../lib/agentStream";

interface TrialResult {
  trial_id: string;
  status: string;
  passed: boolean;
  message: string;
  tools: { name: string; output: unknown }[];
  artifacts: string[];
  interactions: AgentInteraction[];
  missing_tools: string[];
}
export default function SkillTrialPanel({
  draftId,
  modelId,
  disabled,
}: {
  draftId: string;
  modelId: string;
  disabled: boolean;
}) {
  const [result, setResult] = useState<TrialResult | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function perform(path: string, body: unknown) {
    setBusy(true);
    setError("");
    try {
      setResult(
        await request<TrialResult>(path, {
          method: "POST",
          body: JSON.stringify(body),
        }),
      );
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function resume(item: AgentInteraction, command: AgentResumeCommand) {
    await perform(
      `/api/skills/trials/${encodeURIComponent(result!.trial_id)}/resume`,
      { ...command, interaction_id: item.interaction_id },
    );
  }
  return (
    <section className="space-y-2 rounded-lg border border-blue-100 bg-blue-50 p-4 text-sm">
      <h3 className="font-medium">样例试运行</h3>
      <p>
        使用独立数据库与程序生成的 bank.xlsx、ledger.xlsx、input.txt
        副本，验证工具和规则。
      </p>
      <button
        className="rounded-lg border bg-white px-3 py-2 disabled:opacity-40"
        disabled={disabled || busy || !modelId}
        onClick={() =>
          void perform(
            `/api/skills/drafts/${encodeURIComponent(draftId)}/trial`,
            { model_id: modelId },
          )
        }
      >
        {busy ? "试运行中…" : "运行样例副本"}
      </button>
      {error && (
        <p role="alert" className="text-red-700">
          {error}
        </p>
      )}
      {result && (
        <div className="space-y-2">
          <p>
            {result.passed
              ? "样例验证通过"
              : result.status === "waiting"
                ? "等待审批或补充信息"
                : "尚未通过样例验证"}{" "}
            · {result.status}
          </p>
          <p className="text-xs text-zinc-500">{result.message}</p>
          <p>
            调用工具：{result.tools.map((t) => t.name).join("、") || "尚未调用"}
          </p>
          {!!result.missing_tools?.length && (
            <p className="text-amber-800">
              尚未验证所需工具：{result.missing_tools.join("、")}
            </p>
          )}
          <p>产物：{result.artifacts.join("、") || "无文件产物"}</p>
          <AgentInteractionPanel
            items={result.interactions}
            onSubmit={resume}
            disabled={busy}
          />
          <details>
            <summary className="cursor-pointer">查看工具返回</summary>
            <pre className="max-h-60 overflow-auto whitespace-pre-wrap text-xs">
              {JSON.stringify(result.tools, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}
