import {useFeedback} from "../components/ui/Feedback";
import ApplicationTaskStatus from "../components/ApplicationTaskStatus";
import LegacyHistoryNotice from "../components/LegacyHistoryNotice";
import {useWorkspaceDraft,useWorkspaceField,useWorkspaceSet} from "../hooks/useWorkspaceDraft";
import {invalidateFinmod,restoreFinmodTask} from "../lib/workflowDrafts";
import WorkspaceStatus from "../components/WorkspaceStatus";
import ReportHistoryPanel, { type ReportHistoryItem } from "../components/ReportHistoryPanel";
import { useWorkflow } from "../hooks/useWorkflow";
import { useHiddenProject } from "../hooks/useHiddenProject";
/** Independent modeling workspace: goal/data, methods/mappings, chart preferences,
 * calculation/report. Drafts and reports persist in the application project.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, ChartColumn, AlertTriangle, Sparkles, Database, ArrowRight, X, FileSpreadsheet, CheckCircle2, FolderOpen, RotateCcw, History, Wrench, Check, Layers } from "lucide-react";
import type {
  DataFileRecord, FinmodGuideResult, FinmodMetaData, ModelOption, Provider, SkillDetail,
} from "../lib/api";
import {
  finmodMeta, finmodGuide, finmodVarmap, finmodVarmapSuggest, finmodDerivedEval, finmodChartSuggest, finmodRun,
  finmodSaveChart, finmodExportMd, finmodAssetUrl, finmodPrepData,
  updateProject, listDataFiles, fetchSkillDetail,
  type FinmodVarmapResult, type FinmodVarmapModel,
  type FinmodChartSuggestResult, type FinmodChartManifestItem, type FinmodRunResult,
  type FinmodExportMdResult, type FinmodPrepStep, type FinmodPrepResult, type FinmodStylePrefs,
} from "../lib/api";
import ModelCard from "../components/ModelCard";
import ChartLibrary from "../components/ChartLibrary";
import GuideCard from "../components/GuideCard";
import VariableMapTable, { type DerivedFormulaLine } from "../components/VariableMapTable";
import DataPrepPanel from "../components/DataPrepPanel";
import ChartManifest from "../components/ChartManifest";
import ChartPreview from "../components/ChartPreview";
import ChartRenderer from "../components/ChartRenderer";
import DataAnalyzer from "../components/DataAnalyzer";
import AppDataPanel from "../components/AppDataPanel";
import ModelPicker from "../components/ModelPicker";
import MarkdownBlock from "../components/MarkdownBlock";
import ReportWorkspace from "../components/ReportWorkspace";
import {listWorkbenchReports,hideWorkbenchReport} from "../lib/api";
import LoadingState from "../components/LoadingState";
import { buildChartOption, resolveTemplate } from "../components/chart-templates";
import { renderOptionToSvg, renderOptionToPng, sanitizeFileName } from "../lib/chartSvg";
import { sampleDataToRows } from "../components/ChartPreview";
import { finmodPyChart } from "../lib/api";
import { useModelSelection } from "../hooks/useModelSelection";

/** 4 步流程步骤定义（常驻步骤条；R3 4步化，2026-08-23）：
 *  1 诉求与数据（含多文件选择）→ 2 宏观建模与映射（建模方向+变量映射）
 *  → 3 图表风格 → 4 AI 撰写报告（执行+报告合并） */
const FLOW_STEPS = [
  { n: 1, label: "诉求与数据" },
  { n: 2, label: "宏观建模与映射" },
  { n: 3, label: "图表风格" },
  { n: 4, label: "AI 撰写报告" },
];

/** 历史分析报告记录：每条对应一份落盘的报告 md。 */


/** v6.9：走 Python(mplot3d) 生成 PNG 的 3D 模板。报告落盘时用 finmodPyChart
 * 直接生成 PNG（echarts-gl 无法 SVG 矢量，且 surface/line3D 渲染为曲面不符预期）。 */
const PY3D_TEMPLATES = new Set(["waterfall3d", "bar3d", "scatter3d"]);

/** 把 AI 输出的讲解文案转成 markdown 分点排版（v6.14-2）。
 *  LLM 常把「1）xxx 2）yyy 3）zzz」挤成一行 / 用 ··、①②③ 等作分点，react-markdown 会渲染成
 *  一个长 <p>。此处识别常见分点符号，统一转成 `- ` 列表项（每条换行），保证分段分行可读。 */
export function autoListify(text: string): string {
  if (!text) return text;
  // ① 若已含 markdown 列表/换行 → 原样返回（尊重 LLM 结构）
  if (/^\s*[-*]\s+/m.test(text) || /\n\s*[-*]\s+/.test(text) || /^\s*\d+[.、]/m.test(text)) {
    return text;
  }
  // ② 识别中文序号 1）2）3） 或 （1）（2）（3） 或 ①②③ 或 1.2.3.
  const hasZhSeq = /[（(]?\d+[）)]|①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩/.test(text);
  if (hasZhSeq) {
    // 把「x）内容」拆开，前面补 `- `；句号后若不是序号也按句拆分
    // 先按 [数字）]  或 ①②③ 或 （数字） 切分
    const parts = text
      .split(/(?=[（(]?\d+[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[.、])/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (parts.length > 1) {
      return parts.map((p) => `- ${p}`).join("\n");
    }
  }
  // ③ 兜底：按句号/分号断行（每句一行），避免纯文本大段
  const sentences = text
    .split(/(?<=[。；！？!?])\s*/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (sentences.length > 1) {
    return sentences.map((s) => `- ${s}`).join("\n");
  }
  return text;
}

/** 应用专属隐藏项目标识 + 独立工作区（主库零污染） */
const APP_PROJECT_NAME = "__app_finmod__";

/** 机器人图标（应用专用 AI 徽标，与应用一 data-reconcile 的内联 SVG 一致） */
function BotIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-zinc-500">
      <path d="M12 8V4H8" />
      <rect width="16" height="12" x="4" y="8" rx="2" />
      <path d="M2 14h2" />
      <path d="M20 14h2" />
      <path d="M15 13v2" />
      <path d="M9 13v2" />
    </svg>
  );
}

interface Props {
  models: ModelOption[];
  providers: Provider[];
  defaultModel: string;
}

/** 获取（缓存/复用）或创建应用专属隐藏项目。 */

export default function FinModAppPage({
  models,
  providers,
  defaultModel,
}: Props) {
  // 元数据（P1：模型/图表目录）
  const [meta, setMeta] = useState<FinmodMetaData | null>(null);
  const [metaError, setMetaError] = useState("");
  // 应用专用 AI（skill 职责/约束，v6.14-2）
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const {notify}=useFeedback();
  // 流程状态（步骤条常驻；P2 步骤 1-3 可交互）
  const { project: appProject, setProject: setAppProject, error: appError } = useHiddenProject("finmod", defaultModel);
  const workspace=useWorkspaceDraft<Record<string,unknown>>(appProject?.id||'', 'finmod', {}, invalidateFinmod);
  const [workflow,setWorkflow]=useWorkspaceField(workspace,'workflow',{step:1,reached:1});
  const { step: flowStep, reached: maxReached, select: setFlowStep, unlock, advance, reset: resetFlow } = useWorkflow(5, 1, {value:workflow,setValue:setWorkflow});
  // 应用专属项目 + 已导入数据文件

  const [files, setFiles] = useState<DataFileRecord[]>([]);
  const [dbOpen, setDbOpen] = useState(false);
  const [dbClosing, setDbClosing] = useState(false);
  const [dbTick, setDbTick] = useState(0);
  useEffect(()=>{if(workspace.ready)void workspace.checkData();},[dbTick,workspace.ready,workspace.checkData]);
  // R1：参与分析的文件列表（默认全选已上传文件，步骤 1 可在 UI 增删）
  const [selectedFiles, setSelectedFiles] = useWorkspaceField<DataFileRecord[]>(workspace,"selectedFiles",[],value=>invalidateFinmod(value,1));
  // R3：步骤 2 内部子阶段（direction 建模方向 / varmap 变量映射）
  const [macroPhase, setMacroPhase] = useWorkspaceField<"direction" | "varmap">(workspace,"macroPhase","direction");
  // R3：步骤 4 内部子阶段（execute 执行 / report 报告生成）
  const [reportPhase, setReportPhase] = useWorkspaceField<"execute" | "report">(workspace,"reportPhase","execute");
  /** 用户明确取消全部文件时保留空选择，不回退为全选。 */
  const analysisFiles: DataFileRecord[] = useMemo(() => {
    return selectedFiles;
  }, [selectedFiles, workspace.setValue]);
  /** R1：参与分析的文件名列表（传给后端 file_names） */
  const analysisFileNames: string[] = useMemo(
    () => analysisFiles.map((f) => f.file_name),
    [analysisFiles],
  );
  /** R1：切换某文件是否参与分析（多文件建模） */
  const toggleAnalysisFile = useCallback((f: DataFileRecord) => {
    setSelectedFiles((prev) => {
      const exists = prev.some((sf) => sf.file_name === f.file_name);
      if (exists) return prev.filter((sf) => sf.file_name !== f.file_name);
      return [...prev, f];
    });
  }, [workspace.setValue]);
  // 步骤 1 诉求
  const [goal] = useWorkspaceField(workspace,'goal','');
  const setGoal=useCallback((value:string)=>workspace.setValue(previous=>previous.goal===value?previous:{...invalidateFinmod(previous),goal:value}),[workspace.setValue]);
  // 步骤 2 引导结果
  const [guideResult, setGuideResult] = useWorkspaceField<FinmodGuideResult | null>(workspace,"guideResult",null);
  const [guideBusy, setGuideBusy] = useState(false);
  const [guideError, setGuideError] = useState("");
  // 步骤 3 已选模型（model_id Set）
  const [selectedModels, setSelectedModels] = useWorkspaceSet(workspace,"selectedModels",value=>invalidateFinmod(value,2));
  const [expandedModel, setExpandedModel] = useState<string | null>(null);
  // 步骤 4 变量映射（P3）
  const [varmap, setVarmap] = useWorkspaceField<FinmodVarmapResult | null>(workspace,"varmap",null);
  const [varmapBusy, setVarmapBusy] = useState(false);
  const [varmapError, setVarmapError] = useState("");
  /** 当前映射覆盖：{model_id: {var_name: col}}（用户手动选择/预填） */
  const [mappingOverrides, setMappingOverrides] = useWorkspaceField<Record<string, Record<string, string>>>(workspace,"mappingOverrides",{},value=>invalidateFinmod(value,3));
  /** 期间列 */
  const [periodCol, setPeriodCol] = useWorkspaceField(workspace,"periodCol","",value=>invalidateFinmod(value,3));
  /** 派生变量公式行 */
  const [derivedFormulas, setDerivedFormulas] = useWorkspaceField<DerivedFormulaLine[]>(workspace,"derivedFormulas",[],value=>invalidateFinmod(value,3));
  /** v3.5：允许缺失分析（truly_missing 存在时勾选后可放行，报告给出说明） */
  const [allowMissingAnalysis, setAllowMissingAnalysis] = useWorkspaceField(workspace,"allowMissingAnalysis",false);
  const [derivedResults, setDerivedResults] = useWorkspaceField<Record<string, { type: string; sample: unknown[]; rows: number }>>(workspace,"derivedResults",{});
  const [derivedErrors, setDerivedErrors] = useState<{ line: number; name: string; kind: string; message: string }[]>([]);
  const [derivedBusy, setDerivedBusy] = useState(false);
  // v2.0 V2：AI 变量匹配（LLM 推荐 + 置信度 + 统计量绑定）
  const [aiUsed, setAiUsed] = useWorkspaceField(workspace,"aiUsed",false);           // varmap-suggest 是否用过 LLM
  /** AI 推荐映射（key=model_id:var_name） */
  const [aiMappings, setAiMappings] = useWorkspaceField<Record<string, { confidence: number; reason: string; candidates: string[] }>>(workspace,"aiMappings",{});
  /** AI 统计量绑定（key=model_id:var_name） */
  const [aiStatBindings, setAiStatBindings] = useWorkspaceField<Record<string, { stat: string; based_on: string }>>(workspace,"aiStatBindings",{});
  /** 统计量用户绑定（改函数/来源列） */
  const [statBindings, setStatBindings] = useWorkspaceField<{ var_name: string; stat: string; based_on: string }[]>(workspace,"statBindings",[],value=>invalidateFinmod(value,3));
  // V3：数据预处理（数据准备区）
  const [prepSteps, setPrepSteps] = useWorkspaceField<FinmodPrepStep[]>(workspace,"prepSteps",[],value=>invalidateFinmod(value,3));
  const [prepResult, setPrepResult] = useWorkspaceField<FinmodPrepResult | null>(workspace,"prepResult",null);
  const [prepBusy, setPrepBusy] = useState(false);
  // 步骤 5 图表配置（P4）
  const [chartSuggest, setChartSuggest] = useWorkspaceField<FinmodChartSuggestResult | null>(workspace,"chartSuggest",null);
  const [chartBusy, setChartBusy] = useState(false);
  const [chartLevel, setChartLevel] = useWorkspaceField(workspace,"chartLevel","L2");
  const [chartError, setChartError] = useState("");
  // R4：图表风格偏好（用户表达，非硬性勾选；传给 charts-suggest style_prefs）
  const [stylePrefs, setStylePrefs] = useWorkspaceField<FinmodStylePrefs>(workspace,"stylePrefs",{ level: "L2", colors: "auto", categories: [] },value=>invalidateFinmod(value,3));
  /** 图表偏好类别选项（与后端 CHART_CATEGORY_LABELS 对齐） */
  const CHART_CATEGORY_OPTIONS = [
    { id: "trend", label: "趋势" },
    { id: "compare", label: "对比" },
    { id: "share", label: "占比" },
    { id: "sensitivity", label: "敏感性" },
    { id: "distribution", label: "分布" },
    { id: "relation", label: "关系" },
  ];
  /** R4：切换偏好类别（多选） → 重新推荐 */
  const togglePrefCategory = useCallback((cat: string) => {
    setStylePrefs((prev) => {
      const cats = prev.categories || [];
      const next = cats.includes(cat) ? cats.filter((c) => c !== cat) : [...cats, cat];
      return { ...prev, categories: next };
    });
  }, [workspace.setValue]);
  /** AI 推荐卡展开（v6.8：折叠=名称+理由+加入；展开=模板预览+示例数据） */
  const [suggestOpen, setSuggestOpen] = useState<string | null>(null);
  /** 图表确认清单（chart_manifest 收敛，P5 run 消费） */
  const [chartManifest, setChartManifest] = useWorkspaceField<FinmodChartManifestItem[]>(workspace,"chartManifest",[],value=>invalidateFinmod(value,4));
  // 步骤 6 执行（P5）
  const [localRunBusy,setRunBusy]=useState(false);
  const [serverBusy,setServerBusy]=useState(false);
  const runBusy=localRunBusy||serverBusy;
  const [runError, setRunError] = useState("");
  const [runResult, setRunResult] = useWorkspaceField<FinmodRunResult | null>(workspace,"runResult",null);
  const [analyzerOpen, setAnalyzerOpen] = useState(false);
  // 步骤 7 报告交付（P6）
  const [reportBusy, setReportBusy] = useState(false);
  const [reportError, setReportError] = useState("");
  const [reportResult, setReportResult] = useWorkspaceField<FinmodExportMdResult | null>(workspace,"reportResult",null);
  /** 已落盘图表清单 {filename, rel_path, format} */
  const [savedCharts, setSavedCharts] = useWorkspaceField<{ filename: string; rel_path: string; format: string }[]>(workspace,"savedCharts",[]);
  /** 服务端报告历史；移除列表记录会保留文件。 */
  const [reportHistory, setReportHistory] = useState<ReportHistoryItem[]>([]);
  const [historyError,setHistoryError]=useState('');
  // 模型选择器（复用 ModelPicker；P2 引导用同一模型）
  const [model, setModel] = useModelSelection(
    models,
    appProject?.default_model || defaultModel,
    appProject?.id || APP_PROJECT_NAME,
  );
  // ModelPicker onChange 回调（接收 ModelOption → 存 id，并写回隐藏项目）。
  const handleSelectModel = useCallback(async (m: ModelOption) => {
    setModel(m.id);
    if (!appProject) return;
    try {
      await updateProject(appProject.id, { default_model: m.id });
      setAppProject((current) => current ? { ...current, default_model: m.id } : current);
    } catch (cause) {
      notify(`切换模型失败: ${(cause as Error).message}`);
    }
  }, [appProject, setModel, workspace.setValue]);
  const loadedRef = useRef(false);

  // 初始化：确保应用专属项目 + 拉取元数据
  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    finmodMeta()
      .then((d) => setMeta(d))
      .catch((e) => setMetaError((e as Error).message || "元数据加载失败"));
    // 应用专用 AI：拉取 financial-modeling skill 职责/约束（v6.14-2，与应用一致）
    fetchSkillDetail("financial-modeling")
      .then((s) => setSkill(s))
      .catch(() => { /* 拉取失败不阻塞应用 */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.setValue]);

  // 数据文件列表（dbTick 变化时刷新：导入完成/删除后）
  const selectionInitialized=useRef(false);
  const loadFiles = useCallback(() => {
    if (!appProject || !workspace.ready) return;
    listDataFiles(appProject.id)
      .then((r) => {
        const fs = r.files || [];
        setFiles(fs);
        // R1：默认同步全选已上传文件到 selectedFiles（保持引用稳定，避免重复选中）
        setSelectedFiles((prev) => {
          if (!selectionInitialized.current) {
            selectionInitialized.current = fs.length > 0 || Array.isArray(workspace.value.selectedFiles);
            if (!Array.isArray(workspace.value.selectedFiles)) return fs;
          }
          const names=new Set(prev.map(file=>file.file_name));
          return fs.filter(file=>names.has(file.file_name));
        });
      })
      .catch(() => {});
  }, [appProject,workspace.ready, workspace.setValue]);
  useEffect(() => { loadFiles(); }, [loadFiles, dbTick, workspace.setValue]);

  /** 请求关闭数据库抽屉（滑出动画后卸载） */
  const requestCloseDb = useCallback(() => {
    if (!dbOpen || dbClosing) return;
    setDbClosing(true);
    window.setTimeout(() => { setDbOpen(false); setDbClosing(false); }, 260);
  }, [dbOpen, dbClosing, workspace.setValue]);


  /** 运行 AI 引导（P2：诉求 + 列名索引 → 建议 JSON） */
  const runGuide = useCallback(async (g: string) => {
    if (!appProject) return;
    setGuideBusy(true);
    setGuideError("");
    try {
      const r = await finmodGuide(appProject.id, model, g, analysisFileNames);
      setGuideResult(r);
      // 模式 B：AI 推荐卡默认全选（用户可取消，R2 双向收敛）
      // v2.7 数据完备性：变量不足的模型不默认勾选（步骤 2 已校验，
      // 数据齐的才进变量映射——变量映射是辅助确认而非决定性环节）
      if (r.success && r.mode === "B" && r.directions.length) {
        // v3.2 统一口径：ready（无真正缺失）即可默认勾选；可派生也算就绪
        const ready = r.directions
          .filter((d) => !d.var_coverage || d.var_coverage.ready !== false)
          .map((d) => d.model_id);
        // 至少选 1 个（全缺数据时让用户看到推荐但手动决策）
        setSelectedModels(new Set(ready.length ? ready : r.directions.map((d) => d.model_id)));
      }
    } catch (e) {
      setGuideError((e as Error).message || "AI 引导失败");
      setGuideResult(null);
    } finally {
      setGuideBusy(false);
    }
  }, [appProject, model, analysisFileNames, workspace.setValue]);

  /** 步骤 1 下一步：有诉求或有数据才能进入引导 */
  const handleNextToGuide = () => {
    if (!goal.trim() && files.length === 0) return;
    advance(2);
    void runGuide(goal);
  };

  /** 模式 A/C 重新引导（补传数据/表达诉求后） */
  const handleRerunGuide = (g?: string) => {
    const next = g ?? goal;
    setGoal(next);
    advance(2);
    void runGuide(next);
  };

  /** R2：汇总「已选模型」真正缺失的数据需求（供模式 B 数据需求卡 + 重新引导）。
   *  从 guideResult.directions 中筛选 selectedModels 的 var_coverage.truly_missing_details。
   *  returns {models: [{code,name,missing:[{var_name,meaning,unit}],derivable:[...]}], total} */
  const selectedMissingNeeds = useMemo(() => {
    if (!guideResult || guideResult.mode !== "B" || selectedModels.size === 0) {
      return { models: [] as { code: string; name: string; missing: { var_name: string; meaning: string; unit: string }[]; derivable: string[] }[], total: 0 };
    }
    const models = (guideResult.directions || [])
      .filter((d) => selectedModels.has(d.model_id) && d.var_coverage)
      .map((d) => {
        const vc = d.var_coverage!;
        const missing = (vc.truly_missing_details || vc.missing_names?.map((n) => ({ var_name: n, meaning: "", unit: "" })) || [])
          .map((x) => ({ var_name: x.var_name, meaning: x.meaning || "", unit: x.unit || "" }));
        return {
          code: d.code, name: d.name,
          missing,
          derivable: vc.derivable || [],
        };
      })
      .filter((m) => m.missing.length > 0);
    const total = models.reduce((a, m) => a + m.missing.length, 0);
    return { models, total };
  }, [guideResult, selectedModels, workspace.setValue]);

  const toggleModel = useCallback((id: string) => {
    setSelectedModels((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, [workspace.setValue]);

  // ---------- P3：变量映射 ----------
  /** 进入步骤 4 时加载 varmap（按所选模型 + 所选文件自动预填） */
  const loadVarmap = useCallback(async () => {
    if (!appProject || analysisFiles.length === 0 || selectedModels.size === 0) return;
    setVarmapBusy(true);
    setVarmapError("");
    try {
      // v2.0 V2：优先 AI 匹配（LLM 推荐 + 置信度 + 统计量绑定）；LLM 不可用降级确定性
      const r = await finmodVarmapSuggest(
        appProject.id, analysisFiles[0].file_name, Array.from(selectedModels), model, "",
        analysisFileNames,
      );
      setVarmap(r.varmap);
      setAiUsed(r.used_llm);
      // AI 推荐映射（key=model_id:var_name）
      const aiMap: Record<string, { confidence: number; reason: string; candidates: string[] }> = {};
      const aiStat: Record<string, { stat: string; based_on: string }> = {};
      for (const m of r.ai_suggestions.mappings) {
        aiMap[`${m.model_id}:${m.var_name}`] = {
          confidence: m.confidence, reason: m.reason, candidates: m.candidates || [],
        };
      }
      for (const s of r.ai_suggestions.stat_bindings) {
        aiStat[`${s.model_id}:${s.var_name}`] = { stat: s.stat, based_on: s.based_on };
      }
      setAiMappings(aiMap);
      setAiStatBindings(aiStat);
      // AI 高置信映射自动应用（无基础用户零操作；低置信留给用户确认）
      const overrides: Record<string, Record<string, string>> = {};
      for (const m of r.ai_suggestions.mappings) {
        if (m.mapped_col && m.confidence >= 0.85) {
          overrides[m.model_id] = { ...(overrides[m.model_id] || {}), [m.var_name]: m.mapped_col };
        }
      }
      setMappingOverrides((prev) => {
        const merged = { ...prev };
        for (const [mid, mp] of Object.entries(overrides)) {
          merged[mid] = { ...(merged[mid] || {}), ...mp };
        }
        return merged;
      });
      // 统计量绑定：AI 建议 → statBindings（用户可改）
      const sb: { var_name: string; stat: string; based_on: string }[] = [];
      for (const s of r.ai_suggestions.stat_bindings) {
        sb.push({ var_name: s.var_name, stat: s.stat, based_on: s.based_on });
      }
      setStatBindings(sb);
      // v3.4：可派生变量候选公式 → 自动注入派生公式（用户可改/删除）
      if (r.derived_formulas && r.derived_formulas.length) {
        setDerivedFormulas((prev) => {
          const existing = new Set(prev.map((f) => f.text));
          const news = r.derived_formulas!
            .map((df) => ({ id: `df-${df.model_id}-${df.var_name}`, text: df.formula }))
            .filter((f) => !existing.has(f.text));
          return [...prev, ...news];
        });
      }
      // 预填期间列（自动识别第一个）
      if (r.varmap.period_cols.length > 0) setPeriodCol((p) => p || r.varmap.period_cols[0]);
    } catch (e) {
      // 降级：varmap-suggest 失败 → 用确定性 varmap
      try {
        const r = await finmodVarmap(appProject.id, analysisFiles[0].file_name, Array.from(selectedModels), "", analysisFileNames);
        setVarmap(r);
        if (r.period_cols.length > 0) setPeriodCol((p) => p || r.period_cols[0]);
      } catch (e2) {
        setVarmapError((e2 as Error).message || "变量映射加载失败");
      }
    } finally {
      setVarmapBusy(false);
    }
  }, [appProject, analysisFiles, analysisFileNames, selectedModels, model, workspace.setValue]);

  /** 用户修改某模型某变量的映射列 */
  const handleMappingChange = useCallback((modelId: string, varName: string, col: string) => {
    setMappingOverrides((prev) => {
      const next = { ...prev };
      next[modelId] = { ...(next[modelId] || {}), [varName]: col };
      return next;
    });
  }, [workspace.setValue]);

  /** v2.0：统计量变量绑定（用户改函数/来源列） */
  const handleStatBindingChange = useCallback((
    _modelId: string, varName: string, stat: string, basedOn: string,
  ) => {
    setStatBindings((prev) => {
      const others = prev.filter((s) => s.var_name !== varName);
      return [...others, { var_name: varName, stat, based_on: basedOn }];
    });
  }, [workspace.setValue]);

  /** 合并用户覆盖后的映射（展示用；预填 + 用户手动选择） */
  const mergedVarmapModels: FinmodVarmapModel[] = useMemo(() => {
    if (!varmap) return [];
    return varmap.models.map((m: FinmodVarmapModel) => {
      const over = mappingOverrides[m.model_id] || {};
      return {
        ...m,
        variables: m.variables.map((v) => ({
          ...v,
          mapped_col: over[v.var_name] !== undefined ? over[v.var_name] : v.mapped_col,
          status: (over[v.var_name] !== undefined ? over[v.var_name] : v.mapped_col) ? "matched" : "missing",
        })),
      };
    });
  }, [varmap, mappingOverrides, workspace.setValue]);

  /** 派生变量防抖实时重算（300ms） */
  const derivedTimer = useRef<number | null>(null);
  const runDerivedEval = useCallback(async () => {
    if (!appProject || analysisFiles.length === 0) return;
    const formulas = derivedFormulas.map((f) => f.text).filter((t) => t.trim());
    if (formulas.length === 0) {
      setDerivedResults({});
      setDerivedErrors([]);
      return;
    }
    setDerivedBusy(true);
    try {
      const r = await finmodDerivedEval(appProject.id, analysisFiles[0].file_name, formulas, {}, "", analysisFileNames);
      setDerivedResults(r.results);
      setDerivedErrors(r.errors);
    } catch (e) {
      setDerivedErrors([{ line: 0, name: "", kind: "value", message: (e as Error).message || "求值失败" }]);
    } finally {
      setDerivedBusy(false);
    }
  }, [appProject, analysisFiles, analysisFileNames, derivedFormulas, workspace.setValue]);
  useEffect(() => {
    if (derivedTimer.current) window.clearTimeout(derivedTimer.current);
    derivedTimer.current = window.setTimeout(() => { void runDerivedEval(); }, 300);
    return () => { if (derivedTimer.current) window.clearTimeout(derivedTimer.current); };
  }, [derivedFormulas, runDerivedEval, workspace.setValue]);

  // V3：数据预处理防抖预览（步骤链变化 → 调 prep-data）
  const prepTimer = useRef<number | null>(null);
  const runPrepPreview = useCallback(async () => {
    if (!appProject || analysisFiles.length === 0 || prepSteps.length === 0) {
      setPrepResult(null);
      return;
    }
    setPrepBusy(true);
    try {
      const r = await finmodPrepData(appProject.id, analysisFiles[0].file_name, prepSteps, "", analysisFileNames);
      setPrepResult(r);
    } catch (e) {
      setPrepResult({ success: false, columns: [], rows: 0, preview: [],
        logs: [`预览失败：${(e as Error).message}`], errors: [] });
    } finally {
      setPrepBusy(false);
    }
  }, [appProject, analysisFiles, analysisFileNames, prepSteps, workspace.setValue]);
  useEffect(() => {
    if (prepTimer.current) window.clearTimeout(prepTimer.current);
    prepTimer.current = window.setTimeout(() => { void runPrepPreview(); }, 400);
    return () => { if (prepTimer.current) window.clearTimeout(prepTimer.current); };
  }, [prepSteps, runPrepPreview, workspace.setValue]);

  /** 派生公式行操作 */
  const addDerived = useCallback(() => {
    setDerivedFormulas((prev) => [...prev, { id: `f${Date.now()}`, text: "" }]);
  }, [workspace.setValue]);
  const removeDerived = useCallback((id: string) => {
    setDerivedFormulas((prev) => prev.filter((f) => f.id !== id));
  }, [workspace.setValue]);
  const changeDerived = useCallback((id: string, text: string) => {
    setDerivedFormulas((prev) => prev.map((f) => (f.id === id ? { ...f, text } : f)));
  }, [workspace.setValue]);

  // ---------- P4：图表配置 ----------
  /** guide 推荐图表并集（LLM 按诉求定制，charts-suggest 优先使用） */
  const guideCharts = useMemo(() => {
    if (!guideResult?.directions) return [];
    const set = new Set<string>();
    for (const d of guideResult.directions) {
      for (const c of d.recommended_charts || []) set.add(c);
    }
    return Array.from(set);
  }, [guideResult, workspace.setValue]);

  /** 从建议生成 manifest（默认全选；已存在项保留；variant 空回退 template）
   *  R4：保留 importance（primary/secondary/optional）作为「优先建议」标注。 */
  const applySuggestions = useCallback((sug: FinmodChartSuggestResult) => {
    const metaCharts = new Map((meta?.charts || []).map((c) => [c.id, c]));
    setChartManifest((prev) => {
      const existing = new Set(prev.map((m) => m.variant || m.template));
      const added = sug.suggestions
        .filter((s) => !existing.has(s.template))
        .map((s) => {
          const base = resolveTemplate(s.template)?.tpl.name ?? s.template;
          const cmeta = metaCharts.get(base);
          return {
            id: `c${Date.now()}-${s.template}`,
            template: base,
            variant: s.template,
            name: s.name,
            level: s.level,
            variants: cmeta?.variants && cmeta.variants.length > 0 ? cmeta.variants : [s.template],
            color_scheme: "auto",
            data_source: [],
            reason: s.reason,
            importance: s.importance || "secondary",  // R4：优先建议标注
          } as FinmodChartManifestItem;
        });
      return [...prev, ...added];
    });
  }, [meta, workspace.setValue]);

  /** 进入步骤 5 / 切换档位时拉取图表推荐（确定性） */
  const loadChartSuggest = useCallback(async (level: string) => {
    if (selectedModels.size === 0) return;
    setChartBusy(true);
    setChartError("");
    try {
      const r = await finmodChartSuggest(Array.from(selectedModels), guideCharts, level, stylePrefs);
      setChartSuggest(r);
      applySuggestions(r);
    } catch (e) {
      setChartError((e as Error).message || "图表推荐失败");
    } finally {
      setChartBusy(false);
    }
  }, [selectedModels, guideCharts, applySuggestions, stylePrefs, workspace.setValue]);

  /** R4：偏好变化 → 重新拉取图表推荐（同步档位 state） */
  // （偏好类别/配色变化由下方 useEffect 防抖触发；档位变化走 handleLevelChange）

  // R4：偏好（类别/配色）变化 → 重新推荐（防抖 300ms；仅在图表风格步骤激活且有模型时）
  const stylePrefTimer = useRef<number | null>(null);
  useEffect(() => {
    if (stylePrefTimer.current) window.clearTimeout(stylePrefTimer.current);
    stylePrefTimer.current = window.setTimeout(() => {
      if (flowStep === 3 && selectedModels.size > 0) {
        void loadChartSuggest(stylePrefs.level || chartLevel);
      }
    }, 300);
    return () => { if (stylePrefTimer.current) window.clearTimeout(stylePrefTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stylePrefs.categories, stylePrefs.colors, workspace.setValue]);

  /** 进入步骤 3「图表风格」：拉推荐 + 收敛清单 */
  const enterStep5 = useCallback(() => {
    advance(3);
    void loadChartSuggest(chartLevel);
  }, [loadChartSuggest, chartLevel, workspace.setValue]);

  /** 档位切换：重新推荐 + 白名单过滤现有清单 */
  const handleLevelChange = useCallback((level: string) => {
    setChartLevel(level);
    void loadChartSuggest(level);
    // 档位白名单：移除超出档位的图（L1 不保留 L2/L3）
    const allowed = level === "L1" ? ["L1"] : level === "L2" ? ["L1", "L2"] : ["L1", "L2", "L3"];
    setChartManifest((prev) => prev.filter((m) => allowed.includes(m.level)));
  }, [loadChartSuggest, workspace.setValue]);

  /** 微观勾选模板（加入清单） */
  const toggleMicroChart = useCallback((template: string, name: string, level: string, variants: string[]) => {
    setChartManifest((prev) => {
      if (prev.some((m) => m.variant === template)) {
        return prev.filter((m) => m.variant !== template);
      }
      return [...prev, {
        id: `c${Date.now()}-${template}`,
        template: resolveTemplate(template)?.tpl.name ?? template,
        variant: template,
        name,
        level,
        variants: variants.length > 0 ? variants : [template],
        color_scheme: "auto",
        data_source: [],
        reason: "手动勾选",
      } as FinmodChartManifestItem];
    });
  }, [workspace.setValue]);

  /** manifest 操作 */
  const removeChart = useCallback((id: string) => {
    setChartManifest((prev) => prev.filter((m) => m.id !== id));
  }, [workspace.setValue]);
  const moveChart = useCallback((id: string, dir: -1 | 1) => {
    setChartManifest((prev) => {
      const idx = prev.findIndex((m) => m.id === id);
      const next = [...prev];
      const j = idx + dir;
      if (idx < 0 || j < 0 || j >= next.length) return prev;
      [next[idx], next[j]] = [next[j], next[idx]];
      return next;
    });
  }, [workspace.setValue]);
  const changeVariant = useCallback((id: string, variant: string) => {
    setChartManifest((prev) => prev.map((m) => (m.id === id ? { ...m, variant } : m)));
  }, [workspace.setValue]);
  const changeColor = useCallback((id: string, color: string) => {
    setChartManifest((prev) => prev.map((m) => (m.id === id ? { ...m, color_scheme: color } : m)));
  }, [workspace.setValue]);

  /** v3.5：truly_missing（真正缺失的 input 变量）计数——放行需勾选「允许缺失分析」。
   * 判定：direction=input 且 status=missing 且无派生公式覆盖 → truly_missing。 */
  const trulyMissingCount = useMemo(() => {
    if (!mergedVarmapModels.length) return 0;
    return mergedVarmapModels.reduce((acc, m) => {
      return acc + m.variables.filter((v) => {
        if (v.direction === "output" || v.direction === "statistic") return false;
        if (v.status === "matched" || v.status === "statistic" || v.status === "output") return false;
        // input 且 missing：有派生公式覆盖 → 可派生（不算缺失）；否则真正缺失
        return !derivedFormulas.some((f) => f.text.includes(v.var_name));
      }).length;
    }, 0);
  }, [mergedVarmapModels, derivedFormulas, workspace.setValue]);

  /** v3.5 步骤 4 完成检查（软放宽）：无 truly_missing 即就绪；
   *  有 truly_missing 时，需勾选「允许缺失分析」才放行（报告给出说明）。 */
  const varmapReady = useCallback((): boolean => {
    if (!varmap || varmap.models.length === 0) return false;
    // 所有能映射/派生的都就绪，且无真正缺失 → 直接放行
    return trulyMissingCount === 0 || allowMissingAnalysis;
  }, [varmap, trulyMissingCount, allowMissingAnalysis, workspace.setValue]);

  // ---------- P5：确定性执行 ----------
  /** 进入步骤 4「AI 撰写报告」：触发 run（配置清单 → 后端并行计算，0 LLM） */
  const enterStep6 = useCallback(() => {
    if (!appProject || analysisFiles.length === 0 || selectedModels.size === 0 || chartManifest.length === 0) return;
    advance(4);
    setReportPhase("execute");
    setRunResult(null);
    setReportResult(null);
    setSavedCharts([]);
    setRunBusy(true);
    setRunError("");
    // 组装变量映射（mergedVarmapModels：模型变量 → 列）
    const mappings: Record<string, Record<string, string>> = {};
    for (const m of mergedVarmapModels) {
      mappings[m.model_id] = {};
      for (const v of m.variables) {
        if (v.mapped_col) mappings[m.model_id][v.var_name] = v.mapped_col;
      }
    }
    // v2.0 V1：派生/统计量公式 → run 物化（统计量广播整列喂给模型）
    const derived = [...derivedFormulas.map((f) => f.text).filter((t) => t.trim())];
    for (const sb of statBindings) {
      if (sb.based_on) {
        derived.push(`${sb.var_name}=${sb.stat}(${sb.based_on})`);
      }
    }
    finmodRun(
      appProject.id,
      analysisFiles[0].file_name,
      Array.from(selectedModels),
      mappings,
      {},
      chartManifest,
      derived,
      prepSteps,  // V3：数据预处理步骤
      analysisFileNames,  // R1：参与分析的多文件
      periodCol,
    )
      .then((r) => {
        setRunResult(r);
        setRunBusy(false);
        unlock(4);
        // R3：执行完成自动衔接「报告」子阶段（用户点击「AI 撰写报告」生成正文）
        setReportPhase("report");
      })
      .catch((e) => {
        setRunError((e as Error).message || "执行失败");
        setRunBusy(false);
      });
  }, [appProject, analysisFiles, analysisFileNames, selectedModels, chartManifest, mergedVarmapModels, derivedFormulas, statBindings, prepSteps, periodCol, workspace.setValue]);

  // ---------- P6：报告交付（批量 SVG 落盘 → export-md） ----------
  /** 步骤 7「生成报告」：逐图离屏 SVG → save-chart → export-md（LLM 报告 + 路径）。 */
  const runReport = useCallback(async () => {
    if (!appProject || !runResult) return;
    setReportBusy(true);
    setReportError("");
    setSavedCharts([]);
    setReportResult(null);
    try {
      const runId = runResult.runId || runResult.run_id;
      const saved: { filename: string; rel_path: string; format: string }[] = [];
      const chartsWithPath: (FinmodRunResult["charts"][number] & { rel_path?: string })[] = [];
      // 逐图落盘（2D → SVG；3D(Python) → finmodPyChart PNG；均失败跳过）
      for (const ch of runResult.charts) {
        const tpl = ch.variant || ch.template;
        const base = sanitizeFileName(`${ch.name || ch.variant || "chart"}_${ch.chart_id || chartsWithPath.length + 1}`);

        // v6.9：3D 模板改用 Python 生成 PNG（echarts-gl 无矢量、且渲染为曲面不符预期）
        if (PY3D_TEMPLATES.has(tpl) || PY3D_TEMPLATES.has(ch.template)) {
          try {
            const py = await finmodPyChart(tpl, ch.data as unknown as Record<string, unknown>);
            if (py.success && py.b64) {
              const fileName = `${base}.png`;
              const r = await finmodSaveChart(appProject.id, runId, fileName, py.b64, "png");
              saved.push({ filename: r.filename, rel_path: r.rel_path, format: "png" });
              chartsWithPath.push({ ...ch, rel_path: r.rel_path });
              continue;
            }
          } catch { /* Python 3D 失败 → 走下方兜底 */ }
          chartsWithPath.push({ ...ch });
          continue;
        }

        let option = buildChartOption(tpl, ch.data as never, ch.name, undefined);
        if (!option) {
          // 未知模板 → 尝试原始 option（run 已带 data）
          option = buildChartOption("line", ch.data as never, ch.name, undefined);
        }
        if (!option) {
          chartsWithPath.push({ ...ch });
          continue;
        }
        const opt = option as unknown as Record<string, unknown>;
        let content = await renderOptionToSvg(opt);
        let format: "svg" | "png" = "svg";
        if (!content) {
          content = await renderOptionToPng(opt);
          format = "png";
        }
        if (!content) {
          chartsWithPath.push({ ...ch });
          continue;
        }
        const fileName = `${base}.${format}`;
        const r = await finmodSaveChart(appProject.id, runId, fileName, content, format);
        saved.push({ filename: r.filename, rel_path: r.rel_path, format });
        chartsWithPath.push({ ...ch, rel_path: r.rel_path });
      }
      setSavedCharts(saved);
      // export-md（LLM 报告；LLM 不可用 → 后端降级确定性报告）
      // R5：传 data_stats（数据概览）+ style_prefs（图表风格偏好）
      const md = await finmodExportMd({
        projectId: appProject.id,
        runId,
        goal,
        modelId: model,
        models: runResult.models,
        charts: chartsWithPath,
        dataStats: runResult.data_stats as Record<string, unknown> | undefined,
        stylePrefs,
      });
      setReportResult(md);
      await loadReportHistory();
    } catch (e) {
      setReportError((e as Error).message || "报告生成失败");
    } finally {
      setReportBusy(false);
    }
  }, [appProject, runResult, goal, model, stylePrefs, workspace.setValue]);

  async function loadReportHistory() {
    if(!appProject)return;
    try {
      const page=await listWorkbenchReports(appProject.id);
      setReportHistory(page.reports.map(item=>({id:`${item.run_id}:${item.rel_path}`,runId:item.run_id,rel_path:item.rel_path,filename:item.filename,goal:item.goal,createdAt:item.created_at})));
      setHistoryError('');
    }catch(cause){setHistoryError((cause as Error).message);}
  }
  useEffect(()=>{void loadReportHistory();},[appProject?.id]);
  const handleDeleteReport = async (id:string)=>{
    const item=reportHistory.find(report=>report.id===id);
    if(!item||!appProject)return;
    try{await hideWorkbenchReport({project_id:appProject.id,run_id:item.runId,rel_path:item.rel_path,filename:item.filename});await loadReportHistory();}
    catch(cause){setHistoryError((cause as Error).message);}
  };
  const handleRefreshReports=()=>{void loadReportHistory();};

  /** 在【新标签页】打开一份历史报告（复用预览功能，不影响当前页面） */
  const openReportInNewTab = useCallback((item: ReportHistoryItem) => {
    if (!appProject) return;
    const q = new URLSearchParams({
      preview: "1",
      project_id: appProject.id,
      path: item.rel_path,
    });
    window.open(`/?${q.toString()}`, "_blank");
  }, [appProject, workspace.setValue]);

  // ---------- 共享头部 ----------
  const appHeader = (
    <div className="app-header">
      <div className="mx-auto max-w-6xl space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="page-title">财务建模与分析</h1>
            <p className="page-description">
              AI 引导完成「数据 → 模型 → 图表 → Markdown 报告」，计算与渲染 100% 确定性。
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* 模型选择（P2：引导用的 AI 模型） */}
            <div className="rounded-lg border border-zinc-200 bg-white">
              <ModelPicker
                models={models}
                providers={providers}
                value={model}
                onChange={handleSelectModel}
                disabled={guideBusy}
                direction="down"
              />
            </div>
          </div>
        </div>

        {/* 应用专用 AI（折叠卡，v6.14-2：与应用一数据核对一致——点击查看职责与约束） */}
        {skill && (
          <details className="overflow-hidden rounded-xl border border-zinc-200">
            <summary className="flex cursor-pointer select-none items-center gap-2 px-3.5 py-2 transition-colors hover:bg-zinc-50">
              <BotIcon />
              <span className="text-sm font-semibold text-zinc-800">内置标准流程</span>
              <span className="rounded-full bg-zinc-100 px-1.5 py-px text-xs font-medium text-zinc-500">{skill.version ? `版本 ${String(skill.version).slice(0,12)}` : "应用固定流程"}</span>
              <span className="ml-auto text-xs text-zinc-400">点击查看 AI 职责与约束</span>
            </summary>
            <div className="border-t border-zinc-100 px-3.5 py-2.5">
              <p className="text-xs leading-relaxed text-zinc-600">
                本应用内 AI 是「财务建模专用工具」，不是通用智能体：只按流程引导完成数据 → 模型 → 图表 → 报告，
                计算与渲染 100% 确定性，禁止自由发挥、禁止绕过流程确认，使结果可复现。
              </p>
              {skill.purpose && (
                <p className="mt-1.5 text-xs leading-relaxed text-zinc-500">
                  <span className="font-medium text-zinc-700">职责：</span>
                  {skill.purpose}
                </p>
              )}
              {skill.constraints && (
                <p className="mt-1.5 whitespace-pre-line text-xs leading-relaxed text-zinc-500">
                  <span className="font-medium text-zinc-700">约束：</span>
                  {skill.constraints}
                </p>
              )}
            </div>
          </details>
        )}
      </div>
    </div>
  );

  // ---------- 左侧竖排步骤导航 ----------
  const stepNav = (
    <nav className="workflow-nav flex w-44 shrink-0 flex-col border-r bg-white">
      {FLOW_STEPS.map((s, i) => {
        const active = flowStep === s.n;
        const done = maxReached > s.n;
        const interactive = s.n <= maxReached;
        return (
          <div key={s.n} className={i > 0 ? "mt-1" : ""}>
            <button
              onClick={() => { if (interactive) { setFlowStep(s.n); if (s.n === 2) setMacroPhase("direction"); if (s.n === 4) setReportPhase("execute"); } }}
              disabled={!interactive}
              title={interactive ? `回到步骤 ${s.n}：${s.label}` : "尚未到达"}
              className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-sm transition-colors ${
                active
                  ? "bg-zinc-100 font-semibold text-zinc-900"
                  : interactive
                    ? "text-zinc-600 hover:bg-zinc-50"
                    : "cursor-not-allowed text-zinc-300"
              }`}
            >
              <span
                className={`flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                  active ? "bg-zinc-900 text-white" : done ? "bg-emerald-100 text-emerald-600" : interactive ? "bg-zinc-200 text-zinc-500" : "bg-zinc-100 text-zinc-300"
                }`}
              >
                {done ? "✓" : s.n}
              </span>
              {s.label}
              {active && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-zinc-900" />}
            </button>
          </div>
        );
      })}

      {/* 第 5 项：历史记录（独立页面，与上面 4 个步骤同款导航项） */}
      <div className="mt-2 border-t border-zinc-100 pt-2">
        <button
          onClick={() => { setFlowStep(5); }}
          title="历史记录：管理所有历史分析报告"
          className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-sm transition-colors ${
            flowStep === 5 ? "bg-zinc-100 font-semibold text-zinc-900" : "text-zinc-600 hover:bg-zinc-50"
          }`}
        >
          <span className={`flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
            flowStep === 5 ? "bg-zinc-900 text-white" : "bg-zinc-200 text-zinc-500"
          }`}>
            <History size={12} />
          </span>
          历史记录
          {reportHistory.length > 0 && (
            <span className="ml-auto rounded-full bg-blue-50 px-1.5 py-0.5 text-xs font-medium text-blue-600">
              {reportHistory.length}
            </span>
          )}
          {flowStep === 5 && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-zinc-900" />}
        </button>
      </div>
    </nav>
  );

  // ---------- 步骤 1：诉求入口 ----------
  const step1Panel = (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-4">
      <div className="mx-auto max-w-3xl space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-zinc-800">你的分析目标是什么？</h2>
          <p className="mt-0.5 text-xs text-zinc-400">用一句话描述诉求；也可以先上传数据文件，让 AI 根据数据推荐分析方向。</p>
        </div>

        {/* 诉求输入 */}
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="例如：我想看今年利润为什么下降 / 帮我预测明年销售额 / 分析各产品线的盈利能力…"
          rows={3}
          className="w-full resize-none rounded-lg border border-zinc-200 bg-white px-3.5 py-3 text-sm leading-relaxed text-zinc-800 outline-none transition-colors placeholder:text-zinc-400 focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
        />

        {/* 数据文件区 */}
        <div className="rounded-lg border border-zinc-200 bg-white">
          <div className="flex items-center justify-between border-b border-zinc-100 px-4 py-2.5">
            <span className="flex items-center gap-2 text-sm font-medium text-zinc-700">
              <Database size={14} className="text-zinc-400" />
              数据文件
              <span className="text-xs font-normal text-zinc-400">（项目数据库 · 已导入 {files.length} 个）</span>
            </span>
            <button
              onClick={() => setDbOpen(true)}
              className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-500"
            >
              <FileSpreadsheet size={13} />
              管理数据（上传/删除）
            </button>
          </div>
          <div className="px-4 py-3">
            {files.length === 0 ? (
              <p className="py-4 text-center text-sm text-zinc-400">
                还没有数据文件——可点上方「管理数据」上传 Excel；或先填写诉求让 AI 告诉你要准备哪些字段。
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {files.map((f) => {
                  const isSel = analysisFiles.some((sf) => sf.file_name === f.file_name);
                  return (
                    <button
                      key={f.id}
                      onClick={() => toggleAnalysisFile(f)}
                      className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors ${
                        isSel
                          ? "border-blue-300 bg-blue-50 text-blue-700"
                          : "border-zinc-200 bg-zinc-50 text-zinc-500 hover:border-zinc-300"
                      }`}
                    >
                      {isSel ? <Check size={12} className="text-blue-500" /> : <FileSpreadsheet size={12} className="text-zinc-400" />}
                      {f.file_name}
                      <span className="text-xs opacity-70">{f.sheet_count} 表 · {f.total_rows} 行</span>
                    </button>
                  );
                })}
              </div>
            )}
            {analysisFiles.length > 1 && (
              <p className="mt-2 flex items-center gap-1 text-xs text-blue-600">
                <Layers size={11} />
                已选 {analysisFiles.length} 个文件参与分析，将自动合并建模
              </p>
            )}
          </div>
        </div>

        {/* 下一步 */}
        <div className="flex items-center justify-between">
          <p className="text-xs text-zinc-400">
            {goal.trim() || files.length > 0
              ? goal.trim() && files.length > 0
                ? "诉求 + 数据已就绪 → 让 AI 分析（模式 B）"
                : goal.trim()
                  ? "有目标无数据 → AI 将输出数据需求清单（模式 A）"
                  : "有数据无目标 → AI 将给出讲解引导（模式 C）"
              : "请填写诉求或上传数据文件"}
          </p>
          <button
            onClick={handleNextToGuide}
            disabled={!goal.trim() && files.length === 0}
            className={`flex items-center gap-1.5 rounded-lg px-5 py-2 text-sm font-medium transition-colors ${
              goal.trim() || files.length > 0
                ? "bg-zinc-900 text-white hover:bg-zinc-700"
                : "cursor-not-allowed bg-zinc-100 text-zinc-400"
            }`}
          >
            <Sparkles size={14} />
            开始建模
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );

  // ---------- 步骤 2：建模方向（AI 引导 + 模型库补充，v1.9 合并原步骤 2/3） ----------
  const step2Panel = (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-4">
      <div className="mx-auto max-w-4xl space-y-5">
        {!guideBusy && !guideResult && <button className="rounded-lg border bg-white px-4 py-2 text-sm text-blue-700" onClick={()=>void runGuide(goal)}>生成或重新获取建模建议</button>}
        {/* 引导中 loading（点状动画） */}
        {guideBusy && (
          <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm text-zinc-500">
            <LoadingState label="正在对照诉求与数据列名找合适的模型…" />
            <span className="ml-auto text-xs text-zinc-400">1 次模型调用</span>
          </div>
        )}
        {guideError && (
          <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
            <AlertTriangle size={14} />
            {guideError}
          </div>
        )}

        {/* 模式 A：数据需求清单卡 */}
        {!guideBusy && guideResult?.mode === "A" && (
          <div className="rounded-lg border border-amber-200 bg-amber-50/40">
            <div className="border-b border-amber-100 px-4 py-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-zinc-800">
                <AlertTriangle size={14} className="text-amber-500" />
                需要先准备数据：请按下方清单上传 Excel
              </h3>
              <p className="mt-0.5 text-xs text-zinc-500">{guideResult.message || "已分析你的诉求，识别出以下所需字段："}</p>
            </div>
            <div className="px-4 py-3">
              <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr>
                      <th className="border-b border-zinc-200 bg-zinc-50 px-2.5 py-2 text-left font-medium text-zinc-600">字段</th>
                      <th className="border-b border-zinc-200 bg-zinc-50 px-2.5 py-2 text-left font-medium text-zinc-600">类型</th>
                      <th className="border-b border-zinc-200 bg-zinc-50 px-2.5 py-2 text-left font-medium text-zinc-600">示例</th>
                      <th className="border-b border-zinc-200 bg-zinc-50 px-2.5 py-2 text-left font-medium text-zinc-600">用途</th>
                      <th className="border-b border-zinc-200 bg-zinc-50 px-2.5 py-2 text-left font-medium text-zinc-600">建议图表</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(guideResult.data_needs || []).map((n, i) => (
                      <tr key={i} className="border-b border-zinc-100 last:border-b-0">
                        <td className="px-2.5 py-1.5 font-medium text-zinc-800">{n.field}</td>
                        <td className="px-2.5 py-1.5 text-zinc-500">{n.type}</td>
                        <td className="px-2.5 py-1.5 text-zinc-500">{n.example}</td>
                        <td className="px-2.5 py-1.5 text-zinc-500">{n.purpose}</td>
                        <td className="px-2.5 py-1.5">
                          {n.suggested_chart && (
                            <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-xs text-zinc-500">{n.suggested_chart}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 flex items-center gap-2">
                <button
                  onClick={() => setDbOpen(true)}
                  className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
                >
                  <FileSpreadsheet size={13} />
                  上传数据文件
                </button>
                <button
                  onClick={() => handleRerunGuide()}
                  disabled={files.length === 0}
                  className={`flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                    files.length > 0 ? "bg-zinc-900 text-white hover:bg-zinc-700" : "cursor-not-allowed bg-zinc-100 text-zinc-400"
                  }`}
                >
                  <Sparkles size={13} />
                  补齐后重新引导（{files.length > 0 ? `已上传 ${files.length} 个文件` : "待上传"}）
                </button>
              </div>
              <p className="mt-2 text-xs text-zinc-400">提示：上传数据后点「重新引导」，将基于实际列名推荐模型。</p>
            </div>
          </div>
        )}

        {/* 模式 C：讲解卡 + 诉求输入 */}
        {!guideBusy && guideResult?.mode === "C" && (
          <div className="rounded-lg border border-blue-200 bg-blue-50/40">
            <div className="border-b border-blue-100 px-4 py-3">
              <h3 className="flex items-center gap-2 text-sm font-semibold text-zinc-800">
                <BookOpen size={14} className="text-blue-500" />
                财务建模能帮你做什么
              </h3>
              <p className="mt-0.5 text-xs text-zinc-500">先看看模型能做什么；说出目标后即可开始。</p>
            </div>
            <div className="px-4 py-3">
              {guideResult.explanation && (
                <div className="rounded-lg border border-zinc-200 bg-white px-3 py-2.5">
                  {/* v6.14-2：AI 讲解文案自动分点 + 更小字体（页面主流字号 13px） */}
                  <MarkdownBlock
                    text={autoListify(guideResult.explanation)}
                    className="text-sm"
                  />
                </div>
              )}
              <div className="mt-3">
                <textarea
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                  placeholder="现在告诉我你的目标，例如：我想分析回款效率…"
                  rows={2}
                  className="w-full resize-none rounded-lg border border-zinc-200 bg-white px-3 py-2.5 text-sm text-zinc-800 outline-none placeholder:text-zinc-400 focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                />
                <button
                  onClick={() => handleRerunGuide(goal)}
                  disabled={!goal.trim()}
                  className={`mt-2 flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                    goal.trim() ? "bg-zinc-900 text-white hover:bg-zinc-700" : "cursor-not-allowed bg-zinc-100 text-zinc-400"
                  }`}
                >
                  <Sparkles size={13} />
                  说出目标后重新引导
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 降级：LLM 不可用 → 提示 + 直接走模型库（同页） */}
        {!guideBusy && guideResult && !guideResult.success && (
          <div className="rounded-lg border border-amber-200 bg-amber-50/40 px-4 py-3">
            <p className="flex items-center gap-2 text-sm font-medium text-amber-700">
              <AlertTriangle size={14} />
              {guideResult.message || "AI 引导不可用"}
            </p>
            <p className="mt-1 text-xs text-zinc-500">你仍可手动勾选下方模型库的模型完成建模。</p>
          </div>
        )}

        {/* AI 推荐区（模式 B：引导成功出方向，默认勾选） */}
        {!guideBusy && guideResult?.success && guideResult.mode === "B" && (
          <section>
            {/* R2：数据不完备提示 + 数据需求卡（选中模型有真正缺失变量时） */}
            {selectedMissingNeeds.total > 0 && (
              <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50/50">
                <div className="border-b border-amber-100 px-4 py-3">
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-zinc-800">
                    <AlertTriangle size={14} className="text-amber-500" />
                    还需补充 {selectedMissingNeeds.total} 个数据字段
                  </h3>
                  <p className="mt-0.5 text-xs text-zinc-500">
                    已选模型缺少以下变量。补传数据后点「重新引导」，AI 将基于新列名重新推荐方向；字段也可用已有列派生。
                  </p>
                </div>
                <div className="space-y-2 px-4 py-3">
                  {selectedMissingNeeds.models.map((m) => (
                    <div key={`${m.code}-${m.name}`} className="rounded-lg border border-zinc-200 bg-white px-3 py-2">
                      <p className="text-sm font-semibold text-zinc-700">
                        <span className="font-mono text-zinc-400">{m.code}</span> {m.name}
                      </p>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {m.missing.map((v) => (
                          <span key={v.var_name} className="inline-flex items-center gap-1 rounded-md bg-red-50 px-2 py-0.5 text-xs text-red-600">
                            <X size={10} className="opacity-60" />
                            {v.var_name}
                            {v.meaning && <span className="text-red-400">· {v.meaning}</span>}
                          </span>
                        ))}
                        {m.derivable.length > 0 && (
                          <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 text-xs text-emerald-600">
                            可派生：{m.derivable.slice(0, 4).join("、")}{m.derivable.length > 4 ? " 等" : ""}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    <button
                      onClick={() => setDbOpen(true)}
                      className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
                    >
                      <FileSpreadsheet size={13} />
                      上传/补数据文件
                    </button>
                    <button
                      onClick={() => handleRerunGuide()}
                      className="flex items-center gap-1.5 rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700"
                    >
                      <Sparkles size={13} />
                      重新引导（{analysisFiles.length > 0 ? `已选 ${analysisFiles.length} 个文件` : "待补数据"}）
                    </button>
                  </div>
                  <p className="text-xs text-zinc-400">提示：补传数据后需在步骤 1 勾选该文件参与分析，再点「重新引导」。</p>
                </div>
              </div>
            )}

            <div className="mb-2 flex items-center gap-2">
              <Sparkles size={15} className="text-blue-500" />
              <h3 className="text-sm font-semibold text-zinc-800">建议的建模方向</h3>
              <span className="text-xs text-zinc-400">（默认已勾选，可取消；下方模型库可再补充）</span>
            </div>
            {(guideResult.directions || []).length === 0 ? (
              <div className="flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-600">
                <AlertTriangle size={14} />
                没能给出方向建议，可以直接从下方模型库选择
              </div>
            ) : (
              <div className="space-y-2.5">
                {(guideResult.directions || []).map((d, i) => (
                  <GuideCard
                    key={d.model_id}
                    direction={d}
                    selected={selectedModels.has(d.model_id)}
                    onToggle={() => toggleModel(d.model_id)}
                    index={i}
                  />
                ))}
              </div>
            )}
          </section>
        )}

        {/* 模型库补充（A-G 分类勾选；模式 B 或降级后可用） */}
        {!guideBusy && guideResult && (guideResult.success ? guideResult.mode === "B" : true) && (
          <section>
            <div className="mb-2 flex items-center gap-2">
              <BookOpen size={14} className="text-zinc-400" />
              <h2 className="text-sm font-semibold text-zinc-800">模型库补充</h2>
              <span className="text-xs text-zinc-400">32 个模型按 A-G 分类 · 点击卡展开详情 · 勾选加入清单</span>
            </div>
            {metaError && (
              <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
                <AlertTriangle size={14} />
                {metaError}
              </div>
            )}
            {!meta && !metaError && (
              <div className="flex items-center gap-2 py-6 text-sm text-zinc-400">
                <LoadingState label="正在加载模型目录…" />
              </div>
            )}
            {meta && meta.model_categories.map((cat) => (
              <section key={cat.key}>
                <div className="mb-1.5 flex items-baseline gap-2">
                  <span className="flex size-5 items-center justify-center rounded bg-zinc-900 text-xs font-bold text-white">{cat.key}</span>
                  <span className="text-sm font-semibold text-zinc-700">{cat.label}</span>
                  <span className="text-xs text-zinc-400">{cat.models.length} 个</span>
                </div>
                <div className="space-y-1.5">
                  {cat.models.map((m) => (
                    <ModelCard
                      key={m.id}
                      meta={m}
                      expanded={expandedModel === m.id}
                      onToggle={() => setExpandedModel(expandedModel === m.id ? null : m.id)}
                      selected={selectedModels.has(m.id)}
                      onSelect={() => toggleModel(m.id)}
                    />
                  ))}
                </div>
              </section>
            ))}
          </section>
        )}

        {/* 已选清单 + 下一步（变量映射） */}
        {!guideBusy && guideResult && (guideResult.success ? guideResult.mode === "B" : true) && (
          <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-medium text-zinc-500">已选模型（{selectedModels.size} 个）</p>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {selectedModels.size === 0 ? (
                    <span className="text-xs text-zinc-400">至少选择 1 个模型才能继续</span>
                  ) : (
                    Array.from(selectedModels).map((id) => {
                      const m = meta?.models.find((x) => x.id === id);
                      return (
                        <span key={id} className="flex items-center gap-1 rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700">
                          {m ? `${m.code} ${m.name}` : id}
                          <button onClick={() => toggleModel(id)} className="text-blue-400 hover:text-blue-600" title="移除">
                            <X size={12} />
                          </button>
                        </span>
                      );
                    })
                  )}
                </div>
              </div>
              <div className="flex-shrink-0 text-right">
                {selectedMissingNeeds.total > 0 && (
                  <p className="mb-1.5 max-w-[220px] text-xs leading-relaxed text-amber-600">
                    有 {selectedMissingNeeds.total} 个字段待补，可先在变量映射派生或标记缺失
                  </p>
                )}
                <button
                  onClick={() => { unlock(2); setMacroPhase("varmap"); void loadVarmap(); }}
                  disabled={selectedModels.size === 0}
                  className={`shrink-0 rounded-lg px-5 py-2 text-sm font-medium transition-colors ${
                    selectedModels.size > 0 ? "bg-zinc-900 text-white hover:bg-zinc-700" : "cursor-not-allowed bg-zinc-100 text-zinc-400"
                  }`}
                >
                  下一步（变量映射）
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  // ---------- 步骤 3：变量映射（P3） ----------
  // ---------- 步骤 3：图表风格（R4：P4 图表配置增强） ----------
  const step4Panel = (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-4">
      <div className="mx-auto max-w-4xl space-y-5">
        {/* R4 图表风格偏好：复杂度 + 类别偏好 + 配色（用户表达「希望出现什么风格」，非硬性勾选） */}
        <div className="space-y-3 rounded-lg border border-zinc-200 bg-white px-4 py-3.5">
          <div className="flex items-center gap-2">
            <ChartColumn size={15} className="text-zinc-400" />
            <span className="text-sm font-semibold text-zinc-700">图表风格偏好</span>
            <span className="text-xs text-zinc-400">告诉 AI 你希望报告里出现什么风格的图——最终图表构成由 AI 按报告内容决定</span>
          </div>
          {/* 复杂度档位 */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs text-zinc-500">复杂度</span>
            <div className="flex gap-1.5">
              {(["L1", "L2", "L3"] as const).map((lv) => (
                <button
                  key={lv}
                  onClick={() => {
                    setChartLevel(lv);
                    setStylePrefs((p) => ({ ...p, level: lv }));
                    handleLevelChange(lv);
                  }}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                    chartLevel === lv
                      ? lv === "L1" ? "bg-emerald-600 text-white"
                        : lv === "L2" ? "bg-blue-600 text-white"
                          : "bg-violet-600 text-white"
                      : "border border-zinc-200 bg-white text-zinc-500 hover:bg-zinc-100"
                  }`}
                >
                  {lv === "L1" ? "简单" : lv === "L2" ? "中等" : "复杂"}
                </button>
              ))}
            </div>
          </div>
          {/* 类别偏好（多选） */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs text-zinc-500">偏好类型</span>
            <div className="flex flex-wrap gap-1.5">
              {CHART_CATEGORY_OPTIONS.map((c) => {
                const on = (stylePrefs.categories || []).includes(c.id);
                return (
                  <button
                    key={c.id}
                    onClick={() => togglePrefCategory(c.id)}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors ${
                      on ? "bg-blue-600 text-white" : "border border-zinc-200 bg-white text-zinc-500 hover:bg-zinc-100"
                    }`}
                  >
                    {on ? `✓ ${c.label}` : c.label}
                  </button>
                );
              })}
            </div>
            <span className="text-xs text-zinc-400">（多选；命中偏好的图标记为「优先」）</span>
          </div>
          {/* 配色偏好 */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs text-zinc-500">配色</span>
            <select
              value={stylePrefs.colors || "auto"}
              onChange={(e) => setStylePrefs((p) => ({ ...p, colors: e.target.value }))}
              className="rounded-lg border border-zinc-200 bg-white px-2 py-1 text-xs text-zinc-700 outline-none focus:border-blue-400"
            >
              {[
                { v: "auto", l: "自动（蓝橙主色）" },
                { v: "blues", l: "蓝色系渐变" },
                { v: "greens", l: "绿色系渐变" },
                { v: "reds", l: "红色系渐变" },
                { v: "oranges", l: "橙色系渐变" },
                { v: "purples", l: "紫色系渐变" },
                { v: "blacks", l: "黑灰色系" },
              ].map((o) => (
                <option key={o.v} value={o.v}>{o.l}</option>
              ))}
            </select>
            <span className="ml-auto text-xs text-zinc-400">
              {chartManifest.length} 张图已加入{chartSuggest ? ` · ${chartSuggest.note}` : ""}
            </span>
          </div>
        </div>

        {/* AI 推荐图表（来自 charts-suggest：guide 优先 + 模型映射 + 偏好）
            v6.8：与图表库同款展开卡——折叠=名称+理由+加入；展开=模板预览+示例数据
            R4：折叠行增加 importance 徽章（优先/按需/兜底） */}
        <section>
          <div className="mb-2 flex items-center gap-2">
            <Sparkles size={14} className="text-blue-500" />
            <h2 className="text-sm font-semibold text-zinc-800">AI 推荐图表</h2>
            <span className="text-xs text-zinc-400">（默认加入清单，可移除；点击展开看效果）</span>
          </div>
          {chartBusy && (
            <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm text-zinc-500">
              <LoadingState label="正在推荐图表…" />
            </div>
          )}
          {chartError && (
            <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
              <AlertTriangle size={14} />
              {chartError}
            </div>
          )}
          {!chartBusy && chartSuggest && (
            <div className="space-y-2">
              {chartSuggest.suggestions.map((s) => {
                const selected = chartManifest.some((m) => (m.variant || m.template) === s.template);
                const open = suggestOpen === s.template;
                return (
                  <div
                    key={s.template}
                    className={`rounded-lg border transition-colors ${
                      open ? "border-blue-300 bg-white shadow-sm" : "border-zinc-200 bg-white hover:border-zinc-300"
                    }`}
                  >
                    {/* 折叠行：AI 徽章 + 名称 + 档位 + 重要性 + 加入按钮 */}
                    <div className="flex items-center gap-2 px-3 py-2">
                      <Sparkles size={13} className="shrink-0 text-blue-500" />
                      <span className={`rounded px-1 py-0.5 text-xs font-bold ${
                        s.level === "L1" ? "bg-emerald-50 text-emerald-700"
                          : s.level === "L2" ? "bg-blue-50 text-blue-700"
                            : "bg-violet-50 text-violet-700"
                      }`}>
                        {s.level}
                      </span>
                      {/* R4：importance 徽章（优先/按需/兜底）+ 类别 */}
                      <span
                        title="报告生成时 AI 的优先级参考：优先=必出 · 按需=内容需要才出 · 兜底=备选"
                        className={`rounded px-1 py-0.5 text-xs font-medium ${
                          s.importance === "primary"
                            ? "bg-amber-50 text-amber-700"
                            : s.importance === "secondary"
                              ? "bg-zinc-100 text-zinc-500"
                              : "bg-zinc-50 text-zinc-400"
                        }`}
                      >
                        {s.importance === "primary" ? "优先" : s.importance === "secondary" ? "按需" : "兜底"}
                      </span>
                      {s.category_label && (
                        <span className="rounded bg-zinc-50 px-1 py-0.5 text-xs text-zinc-400">{s.category_label}</span>
                      )}
                      <button
                        onClick={() => setSuggestOpen((p) => (p === s.template ? null : s.template))}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left"
                      >
                        <span className="truncate text-sm font-medium text-zinc-800">{s.name}</span>
                        <span className="shrink-0 font-mono text-xs text-zinc-400">{s.template}</span>
                        {s.reason && (
                          <span className="hidden min-w-0 flex-1 truncate text-xs text-zinc-500 sm:block">
                            {s.reason}
                          </span>
                        )}
                      </button>
                      <button
                        onClick={() => {
                          if (selected) {
                            const item = chartManifest.find((m) => m.variant === s.template);
                            if (item) removeChart(item.id);
                          } else {
                            toggleMicroChart(s.template, s.name, s.level, [s.template]);
                          }
                        }}
                        className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-medium transition-colors ${
                          selected ? "bg-blue-600 text-white hover:bg-blue-500" : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200"
                        }`}
                      >
                        {selected ? "已加入 ✓" : "加入清单"}
                      </button>
                    </div>
                    {/* 展开区：模板预览（最终效果）+ 示例数据表（与图表库同款） */}
                    {open && (
                      <div className="border-t border-zinc-100 px-4 py-3">
                        <div className="space-y-3">
                          <div className="min-w-0">
                            <div className="mb-1 flex items-center gap-1.5">
                              <span className="flex items-center gap-1.5 text-xs font-semibold text-zinc-500">
                                <Wrench size={12} className="text-blue-500" />
                                模板预览
                              </span>
                              <span className="text-xs text-zinc-300">此即最终图表效果（示例数据）</span>
                            </div>
                            <ChartPreview template={s.template} height={320} />
                          </div>
                          <div className="min-w-0">
                            <div className="mb-1 text-xs font-semibold text-zinc-400">示例数据</div>
                            <SampleTable template={s.template} />
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* 微观勾选：图表库（按档位 tab 列模板，勾选「希望出现」的具体模板 = 优先建议） */}
        <section>
          <div className="mb-2 flex items-center gap-2">
            <BookOpen size={14} className="text-zinc-400" />
            <h2 className="text-sm font-semibold text-zinc-800">图表库勾选（可选微调）</h2>
            <span className="text-xs text-zinc-400">勾选你「希望出现」的图（优先建议）· AI 报告可按内容增减</span>
          </div>
          <ChartLibrary
            chartLevels={meta?.chart_levels ?? []}
            empty={!meta && !metaError}
            selectable
            selectedSet={new Set(chartManifest.map((m) => m.variant))}
            onSelect={(template, name, level) => {
              const cmeta = meta?.charts.find((c) => c.id === (resolveTemplate(template)?.tpl.name ?? template));
              toggleMicroChart(template, name, level, cmeta?.variants || [template]);
            }}
          />
        </section>

        {/* 确认清单（chart_manifest 收敛；R4：=优先建议清单，AI 报告可增减） */}
        <section>
          <div className="mb-2 flex items-center gap-2">
            <CheckCircle2 size={14} className="text-emerald-500" />
            <h2 className="text-sm font-semibold text-zinc-800">图表构成偏好清单</h2>
            <span className="text-xs text-zinc-400">（{chartManifest.length} 张 · 变体/配色/排序可调 · 作为报告图表构成建议，AI 可按内容增减）</span>
          </div>
          <ChartManifest
            manifest={chartManifest}
            onRemove={removeChart}
            onMove={moveChart}
            onVariantChange={changeVariant}
            onColorChange={changeColor}
          />
        </section>

        {/* 下一步（AI 撰写报告）——至少 1 张图 */}
        <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-zinc-500">
              已确认 {chartManifest.length} 张图
              <span className="ml-2 text-xs text-zinc-400">
                {chartManifest.length > 0 ? "清单已收敛 → 进入 AI 撰写报告（执行+报告）" : "至少选择 1 张图才能继续"}
              </span>
            </p>
            <button
              onClick={enterStep6}
              disabled={chartManifest.length === 0}
              className={`shrink-0 rounded-lg px-5 py-2 text-sm font-medium transition-colors ${
                chartManifest.length > 0 ? "bg-zinc-900 text-white hover:bg-zinc-700" : "cursor-not-allowed bg-zinc-100 text-zinc-400"
              }`}
            >
              下一步（AI 撰写报告）
            </button>
          </div>
        </div>
      </div>
    </div>
  );

  // ---------- 步骤 3：变量映射（P3） ----------
  const step3Panel = (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-4">
      <div className="mx-auto max-w-4xl space-y-4">
        {/* 数据文件选择（P3 简化：取第一个已导入文件；P5 支持多文件） */}
        <div className="flex items-center gap-2">
          <Database size={14} className="text-zinc-400" />
          <span className="text-sm text-zinc-500">数据文件：</span>
          <select
            value={files[0]?.file_name || ""}
            onChange={() => { setVarmap(null); void loadVarmap(); }}
            className="max-w-72 truncate rounded-lg border border-zinc-200 bg-white px-2 py-1 text-sm text-zinc-700 outline-none focus:border-blue-400"
          >
            {files.map((f) => (
              <option key={f.id} value={f.file_name}>{f.file_name}</option>
            ))}
          </select>
          <span className="text-xs text-zinc-400">AI 已按模型变量 + 文件列名预填映射，请确认或修正；统计量（均值/标准差等）自动计算</span>
        </div>

        {/* V3：数据准备（可选：清洗/合并/透视/面板化） */}
        {!varmapBusy && varmap && (
          <DataPrepPanel
            allCols={varmap.all_cols}
            sheetNames={(files[0]?.sheets || []).map((s) => s.sheet_name)}
            steps={prepSteps}
            onChangeSteps={setPrepSteps}
            result={prepResult}
            busy={prepBusy}
          />
        )}

        {/* v2.0 V2：AI 匹配状态提示（v3.6 区分「无需映射」全 output 模型） */}
        {!varmapBusy && varmap && (() => {
          // 全 output/统计量模型（无待映射 input）→ 模型自动计算，非"AI 匹配不可用"
          const needMap = (varmap.models || []).some((m) =>
            (m.variables || []).some((v) => v.direction === "input"));
          const ok = aiUsed || !needMap;
          return (
            <div className={`flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-sm ${
              ok
                ? "border border-emerald-200 bg-emerald-50/50 text-emerald-700"
                : "border border-amber-200 bg-amber-50/50 text-amber-700"
            }`}>
              <Sparkles size={13} />
              {needMap
                ? (aiUsed
                    ? "AI 已匹配变量并绑定统计量（均值/标准差/样本量自动计算）；低置信项可手动调整"
                    : "AI 匹配不可用，已按列名相似度预填；统计量请手动绑定函数与来源列")
                : "模型自动计算，无需外部变量映射"}
            </div>
          );
        })()}

        {varmapBusy && (
          <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm text-zinc-500">
            <LoadingState label="AI 正在识别最契合的变量列…" />
          </div>
        )}
        {varmapError && (
          <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
            <AlertTriangle size={14} />
            {varmapError}
          </div>
        )}
        {!varmapBusy && varmap && (
          <VariableMapTable
            models={mergedVarmapModels}
            allCols={varmap.all_cols}
            periodCols={varmap.period_cols}
            periodCol={periodCol}
            onPeriodChange={setPeriodCol}
            onMappingChange={handleMappingChange}
            statBindings={statBindings}
            onStatBindingChange={handleStatBindingChange}
            aiMappings={aiMappings}
            aiStatBindings={aiStatBindings}
            derivedFormulas={derivedFormulas}
            onAddDerived={addDerived}
            onRemoveDerived={removeDerived}
            onDerivedChange={changeDerived}
            derivedBusy={derivedBusy}
            derivedResults={derivedResults}
            derivedErrors={derivedErrors}
          />
        )}

        {/* 完成条件 */}
        <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-zinc-500">
              {varmap ? varmap.message : "等待变量映射…"}
              <span className="ml-2 text-xs text-zinc-400">
                {varmapReady()
                  ? "所有变量已匹配或可派生 ✓"
                  : `仍有 ${trulyMissingCount} 个变量需补数据——可上传数据或勾选「允许缺失分析」`}
              </span>
            </p>
            <button
              onClick={enterStep5}
              disabled={!varmapReady()}
              className={`shrink-0 rounded-lg px-5 py-2 text-sm font-medium transition-colors ${
                varmapReady() ? "bg-zinc-900 text-white hover:bg-zinc-700" : "cursor-not-allowed bg-zinc-100 text-zinc-400"
              }`}
            >
              下一步（图表风格）
            </button>
          </div>
          {/* v3.5 软警告放行：真正缺失数据时，勾选「允许缺失分析」即可继续（报告会给出说明） */}
          {varmap && trulyMissingCount > 0 && !varmapReady() && (
            <label className="mt-3 flex cursor-pointer items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2 text-xs text-amber-700">
              <input
                type="checkbox"
                checked={allowMissingAnalysis}
                onChange={(e) => setAllowMissingAnalysis(e.target.checked)}
                className="h-3.5 w-3.5 accent-amber-600"
              />
              <span>
                允许缺失分析——有 {trulyMissingCount} 个变量数据未补全，将用空值填充并在报告给出「数据补充说明」
              </span>
            </label>
          )}
        </div>
      </div>
    </div>
  );

  // ---------- 步骤 5：确定性执行（P5） ----------
  const step5Panel = (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-4">
      <div className="mx-auto max-w-4xl space-y-5">
        {/* 执行中 loading */}
        {runBusy && (
          <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-4 text-sm text-zinc-500">
            <LoadingState label={`正在确定性执行 ${selectedModels.size} 个模型（并行计算，0 LLM）…`} />
            <span className="ml-auto text-xs text-zinc-400">TmpRun 快照 · 主库零污染</span>
          </div>
        )}
        {runError && (
          <div className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
            <AlertTriangle size={14} />
            {runError}
          </div>
        )}

        {/* 数据分析器入口（高级用户自由计算） */}
        <button
          onClick={() => setAnalyzerOpen((o) => !o)}
          className="flex w-full items-center justify-between rounded-lg border border-zinc-200 bg-white px-4 py-2.5 text-sm font-medium text-zinc-700 hover:border-blue-300"
        >
          <span className="flex items-center gap-2">
            <Sparkles size={14} className="text-blue-500" />
            {analyzerOpen ? "收起数据分析器" : "+ 打开数据分析器"}
          </span>
          <span className="text-xs font-normal text-zinc-400">自由计算 · 结果直接进报告</span>
        </button>
        {analyzerOpen && appProject && files[0] && (
          <DataAnalyzer
            projectId={appProject.id}
            fileName={files[0].file_name}
            allCols={varmap?.all_cols || []}
          />
        )}

        {/* 执行结果：模型结果摘要 */}
        {runResult && (
          <>
            <section>
              <div className="mb-2 flex items-center gap-2">
                <CheckCircle2 size={14} className="text-emerald-500" />
                <h2 className="text-sm font-semibold text-zinc-800">模型计算结果</h2>
                <span className="text-xs text-zinc-400">（确定性引擎 · 共 {runResult.models.length} 个模型）</span>
              </div>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {runResult.models.map((m) => (
                  <div key={m.model_id} className="rounded-lg border border-zinc-200 bg-white p-3.5">
                    <div className="flex items-center gap-2">
                      <span className="rounded bg-blue-50 px-1.5 py-0.5 text-xs font-bold text-blue-700">{m.code}</span>
                      <span className="text-sm font-semibold text-zinc-800">{m.name}</span>
                      {m.ok ? (
                        <span className="ml-auto rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-600">成功</span>
                      ) : (
                        <span className="ml-auto rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600">失败</span>
                      )}
                    </div>
                    <div className="mt-2 rounded-md bg-zinc-50 px-2.5 py-2 text-xs leading-relaxed text-zinc-600">
                      <pre className="whitespace-pre-wrap font-mono text-xs">{JSON.stringify(m.summary, null, 1).slice(0, 600)}</pre>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* 图表渲染（run 返回的数据 → ChartRenderer） */}
            <section>
              <div className="mb-2 flex items-center gap-2">
                <ChartColumn size={14} className="text-zinc-400" />
                <h2 className="text-sm font-semibold text-zinc-800">图表（{runResult.charts.length} 张）</h2>
                <span className="text-xs text-zinc-400">确定性渲染 · 所见即所得</span>
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {runResult.charts.map((ch) => (
                  <ChartRenderer
                    key={ch.chart_id || ch.variant}
                    spec={{
                      template: ch.variant,
                      title: ch.name,
                      option: {
                        data: ch.data,
                        ...(ch.color_scheme && ch.color_scheme !== "auto" ? { color_scheme: ch.color_scheme } : {}),
                      },
                      data_table: ch.data_table,
                    }}
                  />
                ))}
              </div>
            </section>

            {/* 下一步（报告） */}
            <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-zinc-500">
                  {runResult.models.filter((m) => m.ok).length}/{runResult.models.length} 个模型执行成功 · 图表 {runResult.charts.length} 张
                  <span className="ml-2 text-xs text-zinc-400">确定性执行完成，点击「下一步生成」由 AI 撰写报告</span>
                </p>
                <button
                  onClick={() => { advance(4); setReportPhase("report"); if (!reportBusy) void runReport(); }}
                  disabled={reportBusy}
                  className="shrink-0 rounded-lg bg-zinc-900 px-5 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-400"
                >
                  {reportBusy ? <><LoadingState label="生成中…" /></> : "下一步生成"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );

  // ---------- 历史记录（第 5 步，独立页面） ----------
  const stepHistoryPanel = <ReportHistoryPanel error={historyError} items={reportHistory} onRefresh={handleRefreshReports} onDelete={handleDeleteReport} onOpen={openReportInNewTab}/>;
  // ---------- 步骤 6：报告交付（P6） ----------
  const step6Panel = (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-4">
      <div className="mx-auto max-w-4xl space-y-5">
        {/* 报告生成由明确动作触发；恢复页面不会重复调用模型。 */}
        {!reportResult && (
          <div className="rounded-lg border border-zinc-200 bg-white px-6 py-10">
            {reportBusy ? (
              <div className="flex flex-col items-center gap-4">
                <LoadingState label="AI 正在撰写报告…" />
                <p className="text-xs text-zinc-400">
                  正在落盘图表并让 AI 撰写含公式与图表引用的 Markdown 报告，请稍候
                </p>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <AlertTriangle size={14} className="text-amber-500" />
                <p className="text-sm text-zinc-500">{runResult?'计算结果已保留，可生成分析报告。':'请先完成确定性计算，再生成报告。'}</p>
                {runResult?<button className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white" onClick={()=>void runReport()}>生成分析报告</button>:<button className="rounded-lg border px-4 py-2 text-sm" onClick={()=>setReportPhase('execute')}>返回计算</button>}
              </div>
            )}
            {reportError && (
              <div className="mt-3 flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
                <AlertTriangle size={14} />{reportError}
              </div>
            )}
          </div>
        )}

        {/* 已落盘图表清单 */}
        {savedCharts.length > 0 && (
          <section>
            <div className="mb-2 flex items-center gap-2">
              <FolderOpen size={14} className="text-zinc-400" />
              <h2 className="text-sm font-semibold text-zinc-800">已落盘图表（{savedCharts.length}）</h2>
              <span className="text-xs text-zinc-400">assets/ 目录 · 相对路径已写入报告</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {savedCharts.map((f) => (
                <a
                  key={f.filename}
                  href={finmodAssetUrl(runResult?.runId || runResult?.run_id || "", f.filename)}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-1.5 text-xs text-zinc-600 transition-colors hover:border-blue-300 hover:text-blue-600"
                >
                  {f.format === "svg" ? <ChartColumn size={12} className="text-blue-500" /> : <BookOpen size={12} className="text-amber-500" />}
                  {f.filename}
                </a>
              ))}
            </div>
          </section>
        )}

        {/* 报告预览 = 内嵌 FileEditor（按 project_id + rel_path 打开报告 md，复用预览组件） */}
        {reportResult && appProject && (
          <>
            <ReportWorkspace artifact={{project_id:appProject.id,run_id:runResult?.runId||runResult?.run_id||'',rel_path:reportResult.rel_path,filename:reportResult.filename}} title={reportResult.used_llm?'财务分析报告（AI 撰写）':'财务分析报告（确定性生成）'}/>

            {/* 一键重跑 */}
            <div className="flex items-center justify-between rounded-lg border border-zinc-200 bg-white px-4 py-3">
              <p className="text-xs text-zinc-500">
                报告已生成：<span className="font-medium text-zinc-700">{reportResult.filename}</span>
                <span className="ml-2 text-xs text-zinc-400">（{reportResult.rel_path}）</span>
              </p>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { setFlowStep(3); }}
                  className="flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs text-zinc-600 hover:border-blue-300 hover:text-blue-600"
                >
                  <RotateCcw size={12} />调整后重跑
                </button>
                <button
                  onClick={() => { resetFlow(); setReportResult(null); setSavedCharts([]); setGoal(""); setMacroPhase("direction"); setReportPhase("execute"); }}
                  className="rounded-md bg-zinc-900 px-4 py-1.5 text-xs font-medium text-white hover:bg-zinc-700"
                >
                  新分析
                </button>
              </div>
            </div>
          </>
        )}

        {/* 历史运行：已由四步导航下方「历史报告」区接管（点击新标签页预览，不触发重新生成） */}
      </div>
    </div>
  );

  if (!workspace.ready) return <div className="flex min-w-0 flex-1 flex-col"><WorkspaceStatus draft={workspace}/>{appError&&<p role="alert" className="p-6 text-red-700">{appError}</p>}</div>;
  return (
    <div className="app-page relative flex min-w-0 flex-1 flex-col">
      <WorkspaceStatus draft={workspace}/>
      {appProject&&<ApplicationTaskStatus projectId={appProject.id} currentRunId={runResult?.run_id||runResult?.runId||''} localBusy={localRunBusy||reportBusy} dataRevision={workspace.dataRevision} onActive={setServerBusy} onRestore={task=>{
        if(!task.result)return;
        workspace.setValue(previous=>restoreFinmodTask(previous,task,files));
      }}/> }
      <LegacyHistoryNotice projectId={appProject!.id} kind="finmod" onImported={()=>void loadReportHistory()}/>
      {appHeader}
      <div className="app-workflow-body flex min-h-0 min-w-0 flex-1">
        {stepNav}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {/* R3：步骤 2/步骤 4 内部子阶段分段条（不改变主状态机） */}
          {flowStep === 2 && (
            <div className="flex items-center gap-1 border-b border-zinc-200 bg-white px-5 pt-2.5 pb-0">
              {([
                { id: "direction", label: "① 建模方向" },
                { id: "varmap", label: "② 变量映射" },
              ] as const).map((t) => (
                <button
                  key={t.id}
                  onClick={() => { setMacroPhase(t.id); if (t.id === "varmap") void loadVarmap(); }}
                  className={`rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                    macroPhase === t.id
                      ? "border-blue-500 text-blue-600"
                      : "border-transparent text-zinc-500 hover:text-zinc-700"
                  }`}
                >
                  {t.label}
                  <span className="ml-1.5 text-xs text-zinc-400">
                    {t.id === "varmap" ? `（${selectedModels.size} 模型）` : ""}
                  </span>
                </button>
              ))}
            </div>
          )}
          {flowStep === 4 && (
            <div className="flex items-center gap-1 border-b border-zinc-200 bg-white px-5 pt-2.5 pb-0">
              {([
                { id: "execute", label: "① 确定性执行" },
                { id: "report", label: "② AI 撰写报告" },
              ] as const).map((t) => (
                <button
                  key={t.id}
                  onClick={() => {
                    setReportPhase(t.id);
                    // 仅在无执行结果时点击「执行」才触发 run（已有结果则直接查看，不重跑）
                    if (t.id === "execute" && !runResult && !runBusy) void enterStep6();
                  }}
                  className={`rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                    reportPhase === t.id
                      ? "border-blue-500 text-blue-600"
                      : "border-transparent text-zinc-500 hover:text-zinc-700"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
          {flowStep === 1 && (appProject ? step1Panel : (
            <div className="flex flex-1 items-center justify-center gap-2 text-sm text-zinc-400">
              {appError ? <span className="text-red-500">{appError}</span> : <LoadingState label="正在初始化应用数据区…" />}
            </div>
          ))}
          {/* R3 4步化：步骤 2 = 建模方向 + 变量映射（子阶段切换）；步骤 4 = 执行 + 报告（子阶段切换） */}
          {flowStep === 2 && (macroPhase === "direction" ? step2Panel : step3Panel)}
          {flowStep === 3 && step4Panel}
          {flowStep === 4 && (reportPhase === "execute" ? step5Panel : step6Panel)}
          {flowStep === 5 && stepHistoryPanel}
        </div>
      </div>

      {/* 项目数据库抽屉（上传/表头锚定/索引查看/删除/刷新，复用 AppDataPanel） */}
      {(dbOpen || dbClosing) && appProject && (
        <AppDataPanel
          projectId={appProject.id}
          closing={dbClosing}
          onClose={requestCloseDb}
          onImported={() => setDbTick((t) => t + 1)}
        />
      )}
    </div>
  );
}

/** 示例数据表（v6.8：AI 推荐卡展开区用，与图表库 SampleDataTable 同源 sampleDataToRows） */
function SampleTable({ template }: { template: string }) {
  const { columns, rows } = useMemo(() => sampleDataToRows(template), [template]);
  return (
    <div className="max-h-60 overflow-auto rounded-lg border border-zinc-200 bg-white">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 bg-zinc-50">
          <tr>
            {columns.map((c) => (
              <th key={c} className="border-b border-zinc-200 px-2 py-1 text-left font-medium text-zinc-500">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={i % 2 ? "bg-zinc-50/60" : ""}>
              {r.map((v, j) => (
                <td key={j} className="border-b border-zinc-100 px-2 py-1 text-zinc-600 last:border-b-0">
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
