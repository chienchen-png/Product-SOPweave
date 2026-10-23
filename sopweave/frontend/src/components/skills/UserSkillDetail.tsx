import type { SkillDetail } from "../../lib/api";
import {toolLabel} from '../../lib/toolLabels';
export default function UserSkillDetail({ detail }: { detail: SkillDetail }) {
  const policy = detail.execution_policy;
  return (
    <article className="space-y-5 rounded-xl border border-zinc-200 bg-white p-5 text-sm">
      <header>
        <h2 className="font-semibold">{detail.title || detail.name}</h2>
        <p className="mt-1 text-xs text-zinc-500">
          用户 SOP{detail.version ? ` · v${detail.version}` : " · 待发布"}
        </p>
        {detail.enabled === false && (
          <span className="mt-2 inline-flex rounded bg-zinc-100 px-2 py-1 text-xs text-zinc-600">
            已停用
          </span>
        )}
        <p className="mt-3 whitespace-pre-wrap">{detail.description}</p>
      </header>
      <section>
        <h3 className="mb-2 font-medium">所需工具</h3>
        <div className="flex flex-wrap gap-2">
          {detail.required_tools?.map((tool) => (
            <span
              key={tool}
              title={tool}
              className="rounded bg-blue-50 px-2 py-1 text-xs text-blue-800"
            >
              {toolLabel(tool)}
            </span>
          ))}
        </div>
      </section>
      {!!detail.inputs?.length && (
        <section>
          <h3 className="font-medium">输入</h3>
          <ul>
            {detail.inputs.map((input) => (
              <li key={input.name}>
                {input.name}：{input.description}（
                {input.required ? "必填" : "可选"}）
              </li>
            ))}
          </ul>
        </section>
      )}
      <section>
        <h3 className="mb-2 font-medium">执行流程</h3>
        <ol className="list-decimal space-y-3 pl-5">
          {detail.steps.map((step, index) => (
            <li key={step.n || index}>
              <strong>{step.title}</strong>
              <p className="whitespace-pre-wrap text-zinc-600">
                {(step as unknown as { instruction?: string }).instruction ||
                  step.summary}
              </p>
            </li>
          ))}
        </ol>
      </section>
      {!!detail.outputs?.length && (
        <section>
          <h3 className="font-medium">输出</h3>
          <p>{detail.outputs.join("、")}</p>
        </section>
      )}
      {policy && (
        <section className="rounded bg-amber-50 p-3">
          <h3 className="font-medium">程序执行约束</h3>
          <p>
            币种：{policy.currency || "按任务输入"}；金额小数位：
            {policy.decimal_places}；容差：{policy.tolerance || "按任务指定"}
          </p>
          <p>允许写入：{policy.write_paths.join("、") || "无"}</p>
          <p>
            必须审批：{policy.require_approval_for.join("、") || "沿用工作模式"}
          </p>
          {!!policy.required_inputs.length && (
            <p>必填参数：{policy.required_inputs.join("、")}</p>
          )}
        </section>
      )}
      {!!detail.advisory_rules?.length && (
        <section>
          <h3 className="font-medium">提示性业务规则</h3>
          <ul className="list-disc pl-5">
            {detail.advisory_rules.map((rule, index) => (
              <li key={index}>{rule}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
