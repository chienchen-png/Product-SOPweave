import { useEffect, useRef, useState } from "react";
import {
  createSkillDraft,
  fetchSkillDraft,
  updateSkillDraft,
  publishSkillDraft,
  uploadSkillSource,
  generateSkillDraft,
  cancelSkillGeneration,
  fetchAgentTools,
  type SkillDraft,
  type SkillSpec,
  type SkillRef,
  type ModelOption,
  type ToolCategory,
} from "../../lib/api";
import { useModelSelection } from "../../hooks/useModelSelection";
import { notifySkillsChanged } from "../../lib/skillEvents";
import UserSkillDetail from "./UserSkillDetail";
import SkillTrialPanel from "./SkillTrialPanel";
import Dialog from '../ui/Dialog';

const inputClass =
  "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm disabled:bg-zinc-100";
const buttonClass =
  "ui-button";
const splitLines = (text: string) =>
  text
    .split("\n")
    .map((v) => v.trim())
    .filter(Boolean);

export default function SkillCreateWizard({
  draftId,
  models = [],
  onClose,
  onPublished,
}: {
  draftId?: string;
  models?: ModelOption[];
  onClose: () => void;
  onPublished: (ref: SkillRef) => void;
}) {
  const [draft, setDraft] = useState<SkillDraft | null>(null);
  const [spec, setSpec] = useState<SkillSpec | null>(null);
  const [text, setText] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [catalog, setCatalog] = useState<ToolCategory[]>([]);
  const [model, setModel] = useModelSelection(models, "", "skill-authoring");
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(!!draftId),
    [error, setError] = useState("");
  const [dirty, setDirty] = useState(false),
    [sourceDirty, setSourceDirty] = useState(false),
    [confirmed, setConfirmed] = useState(false),
    [progress, setProgress] = useState("");
  const pending = useRef(false),
    closeRequested = useRef(false),
    controller = useRef<AbortController | null>(null),
    publicationKey = useRef("");
  const apply = (value: SkillDraft) => {
    setDraft(value);
    setSpec(value.spec);
    setDirty(false);
    setConfirmed(false);
    publicationKey.current = "";
    setAnswers(
      Object.fromEntries(value.answers.map((a) => [a.question_id, a.value])),
    );
  };
  useEffect(() => {
    let disposed = false;
    fetchAgentTools("general-agent")
      .then((data) => {
        if (!disposed) setCatalog(data.catalog);
      })
      .catch(() => {});
    if (draftId)
      fetchSkillDraft(draftId)
        .then((value) => {
          if (!disposed) {
            apply(value);
            setText(
              value.source.filter((s) => s.kind === "text").at(-1)?.text || "",
            );
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
      controller.current?.abort();
    };
  }, [draftId]);
  useEffect(() => {
    if (!draft || draft.state !== "generating" || busy) return;
    let disposed = false;
    const timer = setInterval(() => {
      fetchSkillDraft(draft.draft_id)
        .then((value) => {
          if (!disposed && value.state !== "generating") apply(value);
        })
        .catch((e) => {
          if (!disposed) setError(e.message);
        });
    }, 1000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [draft?.draft_id, draft?.state, busy]);
  const action = async (operation: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (cause) {
      if ((cause as Error).name !== "AbortError")
        setError((cause as Error).message);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  const ensureDraft = async () => {
    if (draft) return draft;
    const value = await createSkillDraft({ text });
    apply(value);
    return value;
  };
  const change = (next: SkillSpec) => {
    setSpec(next);
    setDirty(true);
    setConfirmed(false);
  };
  const generate = () =>
    action(async () => {
      let current = await ensureDraft();
      if (current.questions.length || sourceDirty)
        current = await updateSkillDraft(current.draft_id, {
          expected_revision: current.draft_revision,
          ...(sourceDirty ? { text } : {}),
          answers: Object.entries(answers).map(([question_id, value]) => ({
            question_id,
            value,
          })),
        });
      setSourceDirty(false);
      apply(current);
      setConfirmed(false);
      controller.current = new AbortController();
      await generateSkillDraft(
        current,
        model,
        (event) => {
          if (event.type === "draft")
            apply(event.data as unknown as SkillDraft);
          else if (event.type === "started")
            setDraft((value) =>
              value
                ? {
                    ...value,
                    draft_revision: event.draft_revision,
                    state: "generating",
                    job_id: event.job_id,
                  }
                : value,
            );
          else if (event.type === "progress")
            setProgress(String(event.data.message || ""));
        },
        controller.current.signal,
      );
      apply(await fetchSkillDraft(current.draft_id));
    });
  const tools = catalog.flatMap((group) => group.tools);
  const locked = busy || loading || draft?.state === "generating";
  const saveDraft = async () => {
    const existed = !!draft;
    let current = await ensureDraft();
    const changedAnswers = JSON.stringify(answers) !== JSON.stringify(Object.fromEntries(current.answers.map(a => [a.question_id,a.value])));
    if ((dirty && spec) || (existed && sourceDirty) || changedAnswers) {
      current = await updateSkillDraft(current.draft_id, {
        expected_revision: current.draft_revision,
        ...(dirty && spec ? {spec} : {}),
        ...(existed && sourceDirty ? {text} : {}),
        ...(changedAnswers ? {answers:Object.entries(answers).map(([question_id,value]) => ({question_id,value}))} : {}),
      });
    }
    apply(current);
    setSourceDirty(false);
  };
  const close = () => {
    if (loading || draft?.state === 'generating') return;
    if (pending.current) {closeRequested.current=true;return;}
    void action(async () => {
      if (draft || text.trim()) await saveDraft();
      onClose();
    });
  };
  useEffect(() => {
    if (!busy && !loading && closeRequested.current) {
      closeRequested.current=false;
      if (!error) close();
    }
  }, [busy,loading]);
  useEffect(() => {
    if (locked || (!dirty && !sourceDirty) || error) return;
    const timer = window.setTimeout(() => { void action(saveDraft); }, 900);
    return () => window.clearTimeout(timer);
  }, [text, dirty, sourceDirty, locked, error]);
  return (
    <Dialog title="创建或编辑 SOP" onClose={close} showClose={false} className="h-[90vh] max-w-5xl bg-zinc-50">
        <header className="flex flex-wrap items-center justify-between gap-y-2 border-b bg-white p-5">
          <h2 className="font-semibold">创建 / 编辑财务 SOP</h2>
          <span role="status" className="ml-auto mr-3 text-xs text-zinc-500">{busy ? '保存或处理中…' : error ? '保存未完成' : dirty || sourceDirty ? '尚未保存' : draft ? '已保存' : '填写需求后自动保存'}</span>
          <button className={buttonClass} disabled={loading || draft?.state==='generating'} onClick={close}>
            关闭并保留草稿
          </button>
        </header>
        <ol aria-label="SOP 创建阶段" className="grid grid-cols-2 gap-2 border-b bg-white px-5 py-3 text-xs sm:grid-cols-4">
          {['提供需求','AI 整理与补充','审阅流程与规则','试运行与发布'].map((label,index)=>{
            const active=confirmed ? 3 : spec ? 2 : draft?.state==='generating' || draft?.questions.length ? 1 : 0;
            return <li key={label} aria-current={active===index?'step':undefined} className={`rounded-lg px-2 py-2 ${active===index?'bg-blue-50 font-medium text-blue-700':'text-zinc-500'}`}>{index+1}. {label}</li>;
          })}
        </ol>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
          {error && (
            <p
              role="alert"
              className="rounded bg-red-50 p-3 text-sm text-red-700"
            >
              {error}
              {(dirty || sourceDirty) && <button className="ml-3 underline" disabled={locked} onClick={() => void action(saveDraft)}>重试保存</button>}
            </p>
          )}
          {loading && <p>加载草稿中…</p>}
          <fieldset disabled={locked} className="space-y-3">
            <label className="block text-sm">
              需求描述 / 补充要求
              <textarea
                aria-label="需求描述"
                className={inputClass}
                rows={3}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setSourceDirty(true);
                  setConfirmed(false);
                }}
                placeholder="例如：每月核对银行流水与账单，按合同编号匹配，金额差额超过容差时交由我确认。"
              />
            </label>
            <label className="block text-sm">
              上传 SOP 文档
              <input
                aria-label="上传 SOP 文档"
                type="file"
                accept=".md,.txt,.docx,.pdf"
                className={inputClass}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file)
                    void action(async () => {
                      const current = await ensureDraft();
                      apply(await uploadSkillSource(current.draft_id, file));
                    });
                  e.target.value = "";
                }}
              />
            </label>
            <p className="text-xs text-zinc-500">
              支持 MD / TXT、Word DOCX、文本 PDF；单个20 MiB，最多5个、合计50
              MiB。扫描件请先 OCR。
            </p>
            {!!draft?.source.length && (
              <details>
                <summary className="cursor-pointer text-sm">
                  来源预览（{draft.source.length}项）
                </summary>
                {draft.source.map((source, i) => (
                  <div key={i} className="mt-2 rounded-lg border bg-white p-3">
                    <strong className="text-sm">
                      {source.display_name || "需求/对话来源"}
                    </strong>
                    <pre className="max-h-44 overflow-auto whitespace-pre-wrap text-xs">
                      {source.text}
                    </pre>
                    {source.warnings?.map((w) => (
                      <p key={w}>{w}</p>
                    ))}
                  </div>
                ))}
              </details>
            )}
            {draft?.questions.map((question) => (
              <label key={question.question_id} className="block text-sm">
                {question.prompt}
                {question.options.length > 0 ? (
                  <select
                    className={inputClass}
                    value={answers[question.question_id] || ""}
                    onChange={(e) =>
                      setAnswers({
                        ...answers,
                        [question.question_id]: e.target.value,
                      })
                    }
                  >
                    <option value="">请选择</option>
                    {question.options.map((option) => (
                      <option key={option}>{option}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    className={inputClass}
                    value={answers[question.question_id] || ""}
                    onChange={(e) =>
                      setAnswers({
                        ...answers,
                        [question.question_id]: e.target.value,
                      })
                    }
                  />
                )}
              </label>
            ))}
            <div className="flex flex-wrap items-center gap-3">
              <label className="text-sm">
                创作模型{" "}
                <select
                  aria-label="创作模型"
                  className="rounded-lg border px-2 py-1"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                >
                  <option value="">请选择模型</option>
                  {models.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className={buttonClass}
                disabled={!model || dirty}
                onClick={() => void generate()}
              >
                {draft?.questions.length
                  ? "提交答案并重新生成"
                  : "AI 解析并生成草稿"}
              </button>
            </div>
          </fieldset>
          {(busy || draft?.state === "generating") && (
            <div
              role="status"
              className="flex items-center gap-3 text-sm text-blue-700"
            >
              <span>
                {draft?.state === "generating"
                  ? progress || "正在生成草稿…"
                  : "正在处理…"}
              </span>
              {draft?.state === "generating" && draft.job_id && (
                <button
                  className={buttonClass}
                  onClick={() => {
                    void cancelSkillGeneration(draft.draft_id)
                      .then((value) => {
                        controller.current?.abort();
                        apply(value);
                      })
                      .catch((e) => setError(e.message));
                  }}
                >
                  停止生成
                </button>
              )}
            </div>
          )}
          {draft?.job_status?.message && (
            <p className="text-sm text-red-700">{draft.job_status.message}</p>
          )}
          {spec && (
            <fieldset
              disabled={locked}
              className="space-y-4 rounded-lg border bg-white p-4"
            >
              <h3 className="font-medium">3. 审阅流程与规则</h3>
              <label className="block text-sm">
                SOP 名称
                <input
                  aria-label="SOP 名称"
                  className={inputClass}
                  value={spec.title}
                  onChange={(e) => change({ ...spec, title: e.target.value })}
                />
              </label>
              <label className="block text-sm">
                功能说明
                <textarea
                  className={inputClass}
                  value={spec.description}
                  onChange={(e) =>
                    change({ ...spec, description: e.target.value })
                  }
                />
              </label>
              <details>
                <summary className="cursor-pointer text-sm">工具选用</summary>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {tools
                    .filter((tool) => tool.name !== "prepare_skill_draft")
                    .map((tool) => (
                      <label key={tool.name} className="text-xs">
                        <input
                          type="checkbox"
                          checked={spec.required_tools.includes(tool.name)}
                          onChange={(e) => {
                            const selected = e.target.checked
                              ? [...spec.required_tools, tool.name]
                              : spec.required_tools.filter(
                                  (id) => id !== tool.name,
                                );
                            change({
                              ...spec,
                              required_tools: selected,
                              steps: spec.steps.map((step) => ({
                                ...step,
                                tool_ids: step.tool_ids.filter((id) =>
                                  selected.includes(id),
                                ),
                              })),
                            });
                          }}
                        />{" "}
                        {tool.name} · {tool.description}
                      </label>
                    ))}
                </div>
              </details>
              {spec.steps.map((step, index) => (
                <div key={step.id} className="space-y-2 rounded-lg border p-3">
                  <label className="block text-sm">
                    步骤 {index + 1}
                    <input
                      aria-label={`步骤 ${index + 1} 名称`}
                      className={inputClass}
                      value={step.title}
                      onChange={(e) =>
                        change({
                          ...spec,
                          steps: spec.steps.map((s, i) =>
                            i === index ? { ...s, title: e.target.value } : s,
                          ),
                        })
                      }
                    />
                  </label>
                  <textarea
                    aria-label={`步骤 ${index + 1} 内容`}
                    className={inputClass}
                    value={step.instruction}
                    rows={3}
                    onChange={(e) =>
                      change({
                        ...spec,
                        steps: spec.steps.map((s, i) =>
                          i === index
                            ? { ...s, instruction: e.target.value }
                            : s,
                        ),
                      })
                    }
                  />
                  <button
                    className={buttonClass}
                    disabled={spec.steps.length <= 1}
                    onClick={() =>
                      change({
                        ...spec,
                        steps: spec.steps.filter((_, i) => i !== index),
                      })
                    }
                  >
                    删除此步骤
                  </button>
                </div>
              ))}
              <button
                className={buttonClass}
                onClick={() =>
                  change({
                    ...spec,
                    steps: [
                      ...spec.steps,
                      {
                        id: "step-" + crypto.randomUUID().slice(0, 8),
                        title: "新增步骤",
                        instruction: "请填写操作要求",
                        tool_ids: [],
                      },
                    ],
                  })
                }
              >
                增加步骤
              </button>
              <label className="block text-sm">
                输出（每行一个）
                <textarea
                  className={inputClass}
                  value={spec.outputs.join("\n")}
                  onChange={(e) =>
                    change({ ...spec, outputs: splitLines(e.target.value) })
                  }
                />
              </label>
              <div className="grid gap-3 sm:grid-cols-3">
                {(["currency", "tolerance"] as const).map((key) => (
                  <label key={key} className="text-sm">
                    {key === "currency" ? "币种" : "金额容差"}
                    <input
                      className={inputClass}
                      value={spec.execution_policy[key]}
                      onChange={(e) =>
                        change({
                          ...spec,
                          execution_policy: {
                            ...spec.execution_policy,
                            [key]: e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                ))}
                <label className="text-sm">
                  金额小数位
                  <input
                    className={inputClass}
                    type="number"
                    min={0}
                    max={6}
                    value={spec.execution_policy.decimal_places}
                    onChange={(e) =>
                      change({
                        ...spec,
                        execution_policy: {
                          ...spec.execution_policy,
                          decimal_places: Number(e.target.value),
                        },
                      })
                    }
                  />
                </label>
              </div>
              <label className="block text-sm">
                允许写入的相对路径（每行一个）
                <textarea
                  className={inputClass}
                  value={spec.execution_policy.write_paths.join("\n")}
                  onChange={(e) =>
                    change({
                      ...spec,
                      execution_policy: {
                        ...spec.execution_policy,
                        write_paths: splitLines(e.target.value),
                      },
                    })
                  }
                />
              </label>
              <details>
                <summary className="cursor-pointer text-sm">
                  必须审批的操作
                </summary>
                {tools
                  .filter(
                    (t) =>
                      t.risk !== "read" && spec.required_tools.includes(t.name),
                  )
                  .map((tool) => (
                    <label
                      key={tool.name}
                      className="mr-3 inline-block text-xs"
                    >
                      <input
                        type="checkbox"
                        checked={spec.execution_policy.require_approval_for.includes(
                          tool.name,
                        )}
                        onChange={(e) =>
                          change({
                            ...spec,
                            execution_policy: {
                              ...spec.execution_policy,
                              require_approval_for: e.target.checked
                                ? [
                                    ...spec.execution_policy
                                      .require_approval_for,
                                    tool.name,
                                  ]
                                : spec.execution_policy.require_approval_for.filter(
                                    (t) => t !== tool.name,
                                  ),
                            },
                          })
                        }
                      />{" "}
                      {tool.name}
                    </label>
                  ))}
              </details>
              {!!spec.blockers.length && (
                <div role="alert" className="rounded bg-amber-50 p-3 text-sm">
                  发布前需要解决：{spec.blockers.join("；")}
                  。可补充需求后重新生成。
                </div>
              )}
              <button
                className={buttonClass}
                disabled={!dirty || !draft}
                onClick={() =>
                  void action(async () => {
                    apply(
                      await updateSkillDraft(draft!.draft_id, {
                        expected_revision: draft!.draft_revision,
                        spec,
                      }),
                    );
                  })
                }
              >
                保存修改并更新预览
              </button>
            </fieldset>
          )}
          {draft?.import_diff && (
            <details>
              <summary className="cursor-pointer text-sm">
                原始 Skill 与当前预览差异
              </summary>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap text-xs">
                {draft.import_diff}
              </pre>
            </details>
          )}
          {draft && spec && (
            <section className="space-y-3"><h3 className="font-medium">4. 试运行与发布</h3><p className="text-sm text-zinc-500">先检查工具、输出和程序约束，再进行隔离试运行；发布后可在项目任务中选择。</p><SkillTrialPanel
              draftId={draft.draft_id}
              modelId={model}
              disabled={
                locked || dirty || sourceDirty || draft.state !== "ready"
              }
            /></section>
          )}
          {draft?.preview && (
            <section>
              <h3 className="mb-2 font-medium">待发布预览</h3>
              <UserSkillDetail detail={draft.preview} />
            </section>
          )}
        </div>
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t bg-white p-4">
          <label className="text-sm">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={
                locked || dirty || sourceDirty || draft?.state !== "ready"
              }
              onChange={(e) => setConfirmed(e.target.checked)}
            />{" "}
            我已审阅当前流程、工具及写入/审批规则，确认发布
          </label>
          <button
            className="ui-button ui-button-primary"
            disabled={
              !confirmed ||
              locked ||
              dirty ||
              sourceDirty ||
              !draft?.generated_digest ||
              draft.state !== "ready"
            }
            onClick={() =>
              void action(async () => {
                publicationKey.current ||= crypto.randomUUID();
                const result = await publishSkillDraft(
                  draft!,
                  publicationKey.current,
                );
                notifySkillsChanged();
                onPublished(result.ref);
              })
            }
          >
            确认发布
          </button>
        </footer>
    </Dialog>
  );
}
