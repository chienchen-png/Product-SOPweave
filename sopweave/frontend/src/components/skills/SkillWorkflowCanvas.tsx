import type { SkillStep } from "../../lib/api";

const STEP_COLORS = [
  { color: "#2563eb", background: "#eff6ff", border: "#bfdbfe" },
  { color: "#7c3aed", background: "#f5f3ff", border: "#ddd6fe" },
  { color: "#0f766e", background: "#f0fdfa", border: "#99f6e4" },
  { color: "#c2410c", background: "#fff7ed", border: "#fed7aa" },
];

export default function SkillWorkflowCanvas({ steps }: { steps: SkillStep[] }) {
  if (!steps.length) return null;

  return (
    <div role="list" aria-label="SOP 执行流程" className="space-y-0">
      {steps.map((step, index) => {
        const palette = STEP_COLORS[index % STEP_COLORS.length];
        const title = step.title.trim();
        const summary = step.summary.trim();
        const redundantText = title === summary || summary.startsWith(title) || title.startsWith(summary);
        const singleText = !title || !summary || redundantText;
        const body = summary.length >= title.length ? summary : title;
        return (
          <div
            key={step.n}
            role="listitem"
            className="relative grid grid-cols-[36px_minmax(0,1fr)] gap-3 pb-4 last:pb-0"
          >
            <div className="relative flex justify-center">
              <span
                aria-hidden="true"
                className="flex size-7 items-center justify-center rounded-full border text-xs font-semibold"
                style={{ color: palette.color, background: palette.background, borderColor: palette.border }}
              >
                {step.n}
              </span>
              {index < steps.length - 1 && (
                <span aria-hidden="true" className="absolute left-1/2 top-7 -bottom-4 w-px -translate-x-1/2 bg-zinc-200" />
              )}
            </div>
            <article className="min-w-0 rounded-lg border border-zinc-200 bg-white px-3.5 py-3">
              {singleText
                ? <p className="text-sm leading-5 text-zinc-800">{body}</p>
                : <>
                    <h4 className="text-sm font-medium text-zinc-800">{title}</h4>
                    <p className="mt-1 text-xs leading-5 text-zinc-500">{summary}</p>
                  </>}
              {step.branch_point && !!step.branches?.length && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {step.branches.map(branch => (
                    <span key={branch.key} className="rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-[11px] text-zinc-600">
                      {branch.key} · {branch.title}
                    </span>
                  ))}
                </div>
              )}
            </article>
          </div>
        );
      })}
    </div>
  );
}
