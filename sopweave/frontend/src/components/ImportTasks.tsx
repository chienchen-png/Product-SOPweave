import { useEffect, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";
import {
  importAnchor,
  importStatus,
  listProjectTasks,
  resumeImportCalculation,
  type DataTask,
  type WorkbookSheetInfo,
} from "../lib/api";
import Dialog from "./ui/Dialog";
import { useDraftState } from "../hooks/useDraftState";
import WorkbookImportSummary from './WorkbookImportSummary';

interface AnchorSheet {
  name: string;
  classification?: string;
  header_row: number;
  confidence: number;
  columns: string[];
  data_start_row?: number;
  data_end_row?: number;
  start_col?: number;
  end_col?: number;
  excluded_columns?: number[];
}
interface Review {
  sheets: AnchorSheet[];
  previews: Record<string, unknown[][]>;
  sheet_info?: WorkbookSheetInfo[];
  skipped_sheets?: {name:string;classification:string}[];
}
interface Region {
  headerRow: string;
  dataStartRow: string;
  dataEndRow: string;
  startCol: string;
  endCol: string;
  excludedColumns: string;
}
interface Props {
  projectId: string;
  reloadToken?: number;
  onImported: () => void;
}
const fields = [
  ["表头行", "headerRow"],
  ["数据起始行", "dataStartRow"],
  ["数据结束行", "dataEndRow"],
  ["起始列", "startCol"],
  ["结束列", "endCol"],
  ["排除列", "excludedColumns"],
] as const;

/** One persisted import/review workflow for ordinary projects and both apps. */
export default function ImportTasks(props: Props) {
  return <ProjectImportTasks key={props.projectId} {...props} />;
}

function ProjectImportTasks({ projectId, reloadToken = 0, onImported }: Props) {
  const [tasks, setTasks] = useState<DataTask[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<DataTask | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [regions, setRegions, draftError] = useDraftState<
    Record<string, Region>
  >(`import-review:${projectId}:${selected?.id ?? "none"}`, {});
  const [reviewError, setReviewError] = useState("");
  const [formulaTask, setFormulaTask] = useState<DataTask | null>(null);
  const [formulaState, setFormulaState] = useState<Record<string, unknown> | null>(null);
  const [formulaError, setFormulaError] = useState("");
  const [formulaBusy, setFormulaBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const opened = useRef(new Set<string>());
  const previous = useRef(new Map<string, string>());
  const completed = useRef(onImported);
  completed.current = onImported;

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function load() {
      try {
        const result = await listProjectTasks(projectId);
        if (!active) return;
        const imports = result.tasks.filter((task) => task.kind === "import");
        const newlyDone = imports.some(
          (task) =>
            task.status === "done" &&
            previous.current.has(task.id) &&
            previous.current.get(task.id) !== "done",
        );
        previous.current = new Map(
          imports.map((task) => [task.id, task.status]),
        );
        setTasks(imports);
        setError("");
        failures = 0;
        if (newlyDone) completed.current();
        const pending = imports.find(
          (task) =>
            task.status === "needs_anchor" && !opened.current.has(task.id),
        );
        if (pending) {
          opened.current.add(pending.id);
          setSelected((current) => current ?? pending);
        }
        if (imports.some((task) => task.status === "running"))
          timer = setTimeout(() => void load(), 800);
      } catch (error) {
        if (!active) return;
        setError(`读取导入任务失败：${(error as Error).message}`);
        if (++failures < 3) timer = setTimeout(() => void load(), 1500);
      }
    }
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [projectId, reloadToken, refresh]);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setReview(null);
    setReviewError("");
    importStatus(selected.id)
      .then((state) => {
        if (!active) return;
        if (state.status !== "needs_anchor") {
          setReviewError("任务状态已变化，请关闭后刷新导入任务。");
          return;
        }
        const anchor = state.anchor as Review | undefined;
        if (!anchor?.sheets?.length) {
          setReviewError("任务缺少导入区域，请重新导入文件。");
          return;
        }
        const skipped=anchor.sheets.filter(sheet=>sheet.classification==='notes'||sheet.classification==='empty');
        const sheets=anchor.sheets.filter(sheet=>!skipped.includes(sheet));
        if(!sheets.length){setReviewError('工作簿只有说明表或空表，没有可导入的数据区域。');return;}
        const defaults: Record<string, Region> = {};
        for (const sheet of sheets)
          defaults[sheet.name] = {
            headerRow: String((sheet.header_row ?? 0) + 1),
            dataStartRow: String(
              (sheet.data_start_row ?? (sheet.header_row ?? 0) + 1) + 1,
            ),
            dataEndRow:
              sheet.data_end_row != null && sheet.data_end_row >= 0
                ? String(sheet.data_end_row + 1)
                : "",
            startCol: String((sheet.start_col ?? 0) + 1),
            endCol: String(
              (sheet.end_col ?? Math.max(sheet.columns.length - 1, 0)) + 1,
            ),
            excludedColumns: (sheet.excluded_columns || [])
              .map((value) => value + 1)
              .join(","),
          };
        setRegions((saved) =>
          Object.fromEntries(
            Object.entries(defaults).map(([name, defaultsForSheet]) => [
              name,
              Object.fromEntries(
                fields.map(([, key]) => [
                  key,
                  typeof saved?.[name]?.[key] === "string"
                    ? saved[name][key]
                    : defaultsForSheet[key],
                ]),
              ) as unknown as Region,
            ]),
          ),
        );
        setReview({ sheets, previews: anchor.previews || {}, sheet_info:anchor.sheet_info,
          skipped_sheets:anchor.skipped_sheets??skipped.map(sheet=>({name:sheet.name,classification:sheet.classification!})) });
      })
      .catch((error) => {
        if (active) setReviewError((error as Error).message);
      });
    return () => {
      active = false;
    };
  }, [selected, setRegions]);

  async function submit() {
    if (!selected || !review || submitting.current) return;
    const anchors: Record<
      string,
      {
        header_row: number;
        data_start_row: number;
        data_end_row: number;
        start_col: number;
        end_col: number;
        excluded_columns: number[];
      }
    > = {};
    for (const sheet of review.sheets) {
      const config = regions[sheet.name];
      const [header, start, endColumnStart, endColumnEnd] = [
        config.headerRow,
        config.dataStartRow,
        config.startCol,
        config.endCol,
      ].map(Number);
      const end = config.dataEndRow.trim() ? Number(config.dataEndRow) : -1;
      const excluded = config.excludedColumns
        .split(/[,，\s]+/)
        .filter(Boolean)
        .map(Number);
      if (
        [header, start, endColumnStart, endColumnEnd].some(
          (value) => !Number.isSafeInteger(value) || value < 1,
        ) ||
        start <= header ||
        endColumnEnd < endColumnStart ||
        (config.dataEndRow.trim() !== "" &&
          (!Number.isSafeInteger(end) || end < start)) ||
        excluded.some(
          (value) =>
            !Number.isSafeInteger(value) ||
            value < endColumnStart ||
            value > endColumnEnd,
        ) ||
        new Set(excluded).size === endColumnEnd - endColumnStart + 1
      ) {
        setReviewError(
          `工作表「${sheet.name}」的行列范围无效，请填写正整数，数据行应位于表头之后，且至少保留一列。`,
        );
        return;
      }
      anchors[sheet.name] = {
        header_row: header - 1,
        data_start_row: start - 1,
        data_end_row: end === -1 ? -1 : end - 1,
        start_col: endColumnStart - 1,
        end_col: endColumnEnd - 1,
        excluded_columns: [...new Set(excluded)].map((value) => value - 1),
      };
    }
    submitting.current = true;
    setBusy(true);
    setReviewError("");
    try {
      await importAnchor(selected.id, anchors);
      setRegions({});
      previous.current.set(selected.id, "running");
      setSelected(null);
      setRefresh((value) => value + 1);
    } catch (error) {
      setReviewError(`提交失败：${(error as Error).message}`);
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  function applyAll(header: string) {
    setRegions((current) =>
      Object.fromEntries(
        Object.entries(current).map(([name, config]) => [
          name,
          {
            ...config,
            headerRow: header,
            dataStartRow: String(Number(header) + 1),
          },
        ]),
      ),
    );
  }

  async function openFormulaReview(task: DataTask) {
    setFormulaTask(task);
    setFormulaState(null);
    setFormulaError("");
    try {
      const state = await importStatus(task.id);
      if (state.status !== "needs_review") {
        setFormulaError("任务状态已变化，请关闭后刷新导入任务。");
        return;
      }
      setFormulaState(state);
    } catch (error) {
      setFormulaError(`读取公式复核信息失败：${(error as Error).message}`);
    }
  }

  async function resumeFormula(strategy: "cached" | "recalculate") {
    if (!formulaTask || formulaBusy) return;
    setFormulaBusy(true);
    setFormulaError("");
    try {
      await resumeImportCalculation(formulaTask.id, strategy);
      setFormulaTask(null);
      setFormulaState(null);
      setRefresh((value) => value + 1);
    } catch (error) {
      setFormulaError(`提交失败：${(error as Error).message}`);
    } finally {
      setFormulaBusy(false);
    }
  }

  return (
    <>
      {(tasks.length > 0 || error) && (
        <section
          aria-label="导入任务"
          className="max-h-40 shrink-0 space-y-2 overflow-y-auto border-b px-3 py-2 text-xs"
        >
          {error && (
            <p role="alert" className="text-red-600">
              {error}
            </p>
          )}
          {tasks
            .filter((task) => task.status !== "done")
            .map((task) => (
              <div key={task.id} className="space-y-1">
                <p className="break-words font-medium text-zinc-700">
                  {task.path || "Excel"}
                </p>
                {task.status === "needs_anchor" ? (
                  <button
                    className="text-blue-600 hover:underline"
                    onClick={() => setSelected(task)}
                  >
                    确认导入区域
                  </button>
                ) : task.status === "needs_review" ? (
                  <div className="space-y-1">
                    <p className="break-words text-amber-700">
                      {task.error || "公式值需要人工选择处理方式"}
                    </p>
                    <button
                      className="text-blue-600 hover:underline"
                      onClick={() => void openFormulaReview(task)}
                    >
                      处理公式
                    </button>
                  </div>
                ) : (
                  <p
                    className={
                      task.status === "failed"
                        ? "break-words text-red-600"
                        : "break-words text-zinc-500"
                    }
                  >
                    {task.status === "running"
                      ? `导入中 ${Math.round(task.progress || 0)}%`
                      : task.error || "导入失败"}
                  </p>
                )}
              </div>
            ))}
          <button
            className="text-zinc-500 hover:underline"
            onClick={() => setRefresh((value) => value + 1)}
          >
            刷新导入任务
          </button>
        </section>
      )}
      {selected && (
        <Dialog
          title="确认导入区域"
          onClose={() => {
            if (!submitting.current) setSelected(null);
          }}
          className="max-w-3xl"
        >
          <div className="shrink-0 border-b p-4 pr-12">
            <h2 className="text-sm font-semibold">确认导入区域</h2>
            <p className="mt-1 break-words text-xs text-zinc-500">
              {selected.path} · 行列从 1
              开始；结束行留空表示自动识别，排除列用逗号分隔。
            </p>
          </div>
          <div className="min-h-0 space-y-4 overflow-y-auto p-4">
            <WorkbookImportSummary sheets={review?.sheet_info} skippedSheets={review?.skipped_sheets}/>
            {!review && !reviewError && (
              <p className="text-sm text-zinc-500">加载预览…</p>
            )}
            {review?.sheets.map((sheet) => (
              <fieldset
                key={sheet.name}
                className="min-w-0 rounded-lg border p-3"
              >
                <legend className="px-1 text-sm font-medium">
                  {sheet.name}
                </legend>
                <div className="max-h-40 overflow-auto">
                  <table className="w-full border-collapse text-xs">
                    <tbody>
                      {(review.previews[sheet.name] || []).map((row, ri) => (
                        <tr key={ri} className="border-b">
                          <td className="bg-zinc-50 px-2 py-1 text-zinc-400">
                            {ri + 1}
                          </td>
                          {row.map((cell, ci) => (
                            <td
                              key={ci}
                              className="max-w-48 truncate px-2 py-1"
                              title={String(cell ?? "")}
                            >
                              {String(cell ?? "")}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 grid grid-cols-3 gap-3">
                  {fields.map(([label, key]) => (
                    <label key={key} className="text-xs text-zinc-600">
                      {label}
                      <input
                        aria-label={`${sheet.name} ${label}`}
                        type={key === "excludedColumns" ? "text" : "number"}
                        min={1}
                        step={1}
                        disabled={busy}
                        placeholder={key === "dataEndRow" ? "自动" : undefined}
                        value={regions[sheet.name]?.[key] || ""}
                        onChange={(e) =>
                          setRegions((current) => ({
                            ...current,
                            [sheet.name]: {
                              ...current[sheet.name],
                              [key]: e.target.value,
                            },
                          }))
                        }
                        className="mt-1 w-full rounded-md border px-2 py-1.5"
                      />
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
          </div>
          <div className="shrink-0 space-y-3 border-t p-4">
            {draftError && (
              <p role="alert" className="text-xs text-red-600">
                {draftError}
              </p>
            )}
            {reviewError && (
              <p role="alert" className="text-xs text-red-600">
                {reviewError}
              </p>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2">
              {review && (
                <button
                  disabled={busy}
                  className="ui-button text-xs"
                  onClick={() => {
                    const first = review.sheets[0];
                    if (first) applyAll(regions[first.name].headerRow);
                  }}
                >
                  全部套用第一个表头行
                </button>
              )}
              <div className="ml-auto flex gap-2">
                <button
                  disabled={busy}
                  className="ui-button"
                  onClick={() => setSelected(null)}
                >
                  稍后确认
                </button>
                <button
                  disabled={busy || !review}
                  className="ui-button ui-button-primary"
                  onClick={() => void submit()}
                >
                  {busy ? "提交中…" : "确认并继续导入"}
                </button>
              </div>
            </div>
          </div>
        </Dialog>
      )}
      {formulaTask && (
        <Dialog
          title="处理公式值"
          onClose={() => {
            if (!formulaBusy) setFormulaTask(null);
          }}
          className="max-w-lg"
        >
          <div className="space-y-4 p-5 pt-10">
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <div className="flex items-start gap-2 text-sm text-amber-900">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                <div className="space-y-1">
                  <p className="font-medium">{formulaTask.path || "Excel 工作簿"}</p>
                  <p className="break-words text-xs">
                    {(formulaState?.error as string) || "公式值未通过自动验证。"}
                  </p>
                </div>
              </div>
            </div>
            {!formulaState && !formulaError && (
              <p className="text-sm text-zinc-500">读取公式状态…</p>
            )}
            <p className="text-xs leading-5 text-zinc-600">
              受控重算会在工作簿副本上重新计算并再次校验。现有缓存只有在值完整且明确接受时才可继续；源 Excel 不会被覆盖。
            </p>
            {formulaError && (
              <p role="alert" className="text-sm text-red-700">
                {formulaError}
              </p>
            )}
            <div className="flex flex-wrap justify-end gap-2">
              <button
                disabled={formulaBusy}
                className="ui-button"
                onClick={() => setFormulaTask(null)}
              >
                稍后处理
              </button>
              <button
                disabled={formulaBusy || !formulaState}
                className="ui-button"
                onClick={() => void resumeFormula("recalculate")}
              >
                重新执行受控重算
              </button>
              {(() => {
                const anchor = (formulaState?.anchor || {}) as Record<string, unknown>;
                const calculation = (anchor.calculation || {}) as Record<string, unknown>;
                const canAcceptCached =
                  calculation.stale_cache === true &&
                  Number(calculation.missing_after || 0) === 0 &&
                  Number(calculation.errors_after || 0) === 0;
                return canAcceptCached ? (
                  <button
                    disabled={formulaBusy}
                    className="ui-button"
                    onClick={() => void resumeFormula("cached")}
                  >
                    使用现有缓存并标记未验证
                  </button>
                ) : null;
              })()}
            </div>
          </div>
        </Dialog>
      )}
    </>
  );
}
