import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SkillDetail } from "../../lib/api";
import { TOOL_GROUP_LABELS } from "../AgentToolCatalog";
import SkillWorkflowCanvas from "./SkillWorkflowCanvas";

export default function BuiltinSkillDetail({
  detail,
}: {
  detail: SkillDetail;
}) {
  return (
    <article className="space-y-5 rounded-xl border border-zinc-200 bg-white p-5">
      <header>
        <h2 className="font-semibold">{detail.title || detail.name}</h2>
        <span className="text-xs text-zinc-500">内置 · 只读</span>
        <p className="mt-2 text-sm text-zinc-600">{detail.description}</p>
      </header>
      <section>
        <h3 className="mb-2 text-sm font-medium">按需工具组</h3>
        <p className="text-sm text-zinc-600">
          {detail.toolsets.map((g) => TOOL_GROUP_LABELS[g] || g).join("、")}
        </p>
      </section>
      {detail.capabilities?.models.length > 0 && (
        <section>
          <h3>模型能力</h3>
          {detail.capabilities.models.map((m) => (
            <p key={m.category} className="text-sm">
              {m.name}：{m.items.join("、")}
            </p>
          ))}
        </section>
      )}
      {detail.capabilities?.charts.length > 0 && (
        <section className="text-sm">
          图表：{detail.capabilities.charts.join("、")}
        </section>
      )}
      {!detail.steps.length && <div className="text-sm leading-relaxed"><Markdown remarkPlugins={[remarkGfm]}>{detail.body}</Markdown></div>}
      {detail.steps.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-medium">工作流</h3>
          <SkillWorkflowCanvas steps={detail.steps} />
        </section>
      )}
      {detail.constraints && (
        <section>
          <h3 className="text-sm font-medium">约束</h3>
          <Markdown remarkPlugins={[remarkGfm]}>{detail.constraints}</Markdown>
        </section>
      )}
      {!!detail.steps.length && <details className="text-xs text-zinc-500"><summary className="cursor-pointer">高级：查看原始 Markdown 定义</summary><div className="mt-3 whitespace-pre-wrap rounded bg-zinc-50 p-3"><Markdown remarkPlugins={[remarkGfm]}>{detail.body}</Markdown></div></details>}
    </article>
  );
}
