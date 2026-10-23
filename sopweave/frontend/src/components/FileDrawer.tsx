import Dialog from "./ui/Dialog";
import {useFeedback} from "./ui/Feedback";
/** FileDrawer — 文件浏览器（阶段 4：完整文件操作）。
 *
 * 功能：
 * - 目录树递归展开
 * - 右键菜单：新建文件/目录、重命名、复制、剪切、粘贴、删除、导入数据库、刷新
 * - 顶部工具栏：新建、刷新
 * 对接：/api/files/*（routes/files.py）
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  X, ChevronRight, ChevronDown, File, Folder, RefreshCw,
  FilePlus, FolderPlus, FileUp, FolderUp, Pencil, Copy, Scissors, Clipboard,
  Trash2, Database, Eye, Upload, Plus,
} from "lucide-react";
import {
  fetchFileTree, createFileItem, renameFileItem, copyFileItem,
  deleteFileItem, importDataFile, uploadFile,
  fileMeta, openExternalFile,
  type FileEntry, type FileTreeResponse,
} from "../lib/api";

const FILE_PAGE_SIZE = 200;

interface CtxMenu {
  x: number;
  y: number;
  entry: FileEntry;
}

interface FileDrawerProps {
  projectId: string;
  workDir: string;
  closing?: boolean; // 阶段 7.9.10.2：父级触发滑出动画中（渲染 drawer-anim-out）
  onClose: () => void;
  onPick: (path: string) => void;
  onRefer: (path: string) => void;
  onImported: () => void;
}

export default function FileDrawer(props: FileDrawerProps) {
  return <ProjectFileDrawer key={props.projectId} {...props} />;
}

function ProjectFileDrawer({projectId, workDir, closing, onClose, onPick, onRefer, onImported}: FileDrawerProps) {
  const {notify,confirm}=useFeedback();
  const [tree, setTree] = useState<FileEntry[]>([]);
  const [treeTotal, setTreeTotal] = useState(0);
  const [treeNextCursor, setTreeNextCursor] = useState("");
  const [loadingMoreRoot, setLoadingMoreRoot] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState<Record<string, string[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [ctx, setCtx] = useState<CtxMenu | null>(null);
  const [clipboard, setClipboard] = useState<{ op: "copy" | "cut"; path: string } | null>(null);
  const [prompt, setPrompt] = useState<{ title: string; placeholder: string; onSubmit: (val: string) => Promise<void> } | null>(null);
  // 上传状态（阶段 7.9：上传文件/文件夹）
  const [uploading, setUploading] = useState(false);
  const uploadInFlight = useRef(false);
  const [uploadResult, setUploadResult] = useState<{ok: number; failures: {path: string; reason: string}[]} | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  // 顶栏新建的当前目录（最近选中/右键的目录）
  const [activeDir, setActiveDir] = useState("");
  // 阶段 7.9.2：顶栏下拉菜单（new=新建 二级菜单 / upload=上传 二级菜单）
  const [topMenu, setTopMenu] = useState<"new" | "upload" | null>(null);
  const topMenuRef = useRef<HTMLDivElement>(null);
  // 阶段 7.9.2：右键菜单动态定位（Windows 式向下/向上翻转）+ 实际高度测量
  const [ctxPos, setCtxPos] = useState<{ left: number; top: number } | null>(null);
  const ctxMenuRef = useRef<HTMLDivElement>(null);
  const requestId = useRef(0);
  const [revision, setRevision] = useState(0);
  const loadChildren = useCallback(async (path: string, offset = 0) => {
    const data = await fetchFileTree(projectId, path, FILE_PAGE_SIZE, offset);
    setWarnings(current => ({...current, [path]: data.warnings || []}));
    return data;
  }, [projectId]);
  const load = useCallback(async () => {
    const current = ++requestId.current;
    setLoading(true);
    setError("");
    try {
      const data = await fetchFileTree(projectId, "", FILE_PAGE_SIZE);
      if (current !== requestId.current) return;
      setTree(data.entries || []);
      setTreeTotal(data.total || 0);
      setTreeNextCursor(data.next_cursor || "");
      setWarnings({"": data.warnings || []});
      setRevision(previous => previous + 1);
    } catch (e) {
      if (current !== requestId.current) return;
      setError((e as Error).message);
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [projectId]);

  const loadMoreRoot = useCallback(async () => {
    if (!treeNextCursor || loadingMoreRoot) return;
    setLoadingMoreRoot(true);
    try {
      const data = await fetchFileTree(
        projectId,
        "",
        FILE_PAGE_SIZE,
        Number(treeNextCursor),
      );
      setTree(previous => [...previous, ...(data.entries || [])]);
      setTreeTotal(data.total || treeTotal);
      setTreeNextCursor(data.next_cursor || "");
      setWarnings(current => ({...current, "": data.warnings || []}));
    } catch (e) {
      notify(`加载更多失败: ${(e as Error).message}`);
    } finally {
      setLoadingMoreRoot(false);
    }
  }, [projectId, treeNextCursor, treeTotal, loadingMoreRoot, notify]);

  useEffect(() => {
    load();
    return () => { requestId.current++; };
  }, [load]);

  // 点击空白处关闭右键菜单 / 顶栏下拉
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      setCtx(null);
      if (topMenuRef.current && !topMenuRef.current.contains(e.target as Node)) {
        setTopMenu(null);
      }
    };
    window.addEventListener("click", handler);
    return () => window.removeEventListener("click", handler);
  }, []);

  // 阶段 7.9.2：右键菜单动态定位（Windows 式翻转）——测量菜单实际高度，
  // 下方放不下则向上弹出；同时做左右钳制。菜单渲染后执行。
  useLayoutEffect(() => {
    if (!ctx || !ctxMenuRef.current) {
      setCtxPos(null);
      return;
    }
    const h = ctxMenuRef.current.offsetHeight || 380;
    const w = ctxMenuRef.current.offsetWidth || 176;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const MARGIN = 8;
    // 左：钳制在视口内
    const left = Math.max(MARGIN, Math.min(ctx.x, vw - w - MARGIN));
    // 上：下方放不下且上方够 → 向上弹；否则向下（钳制）
    const spaceBelow = vh - ctx.y;
    let top: number;
    if (spaceBelow < h + MARGIN && ctx.y > h + MARGIN) {
      top = Math.max(MARGIN, ctx.y - h); // 向上弹
    } else {
      top = Math.min(ctx.y, vh - h - MARGIN); // 向下弹（钳制）
    }
    setCtxPos({ left, top });
  }, [ctx]);

  const toggle = (path: string) => {
    setActiveDir(path);
    setExpanded((s) => {
      const next = new Set(s);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  // ---------- 操作 ----------

  async function handleCreate(type: "file" | "directory", parentPath: string) {
    setPrompt({
      title: type === "file" ? "新建文件" : "新建文件夹",
      placeholder: type === "file" ? "文件名（如 readme.md）" : "文件夹名",
      onSubmit: async (name) => {
        const full = parentPath ? `${parentPath}/${name}` : name;
        await createFileItem(projectId, full, type);
        await load();
        notify(`已创建${type === "file" ? "文件" : "文件夹"}「${name}」`);
      },
    });
  }

  // 阶段 7.9.5：启动内联重命名
  function startRename(entry: FileEntry) {
    setRenamingEntry(entry);
    setCtx(null);
  }

  // 阶段 7.9.5：确认重命名（Enter/失焦）
  async function commitRename(entry: FileEntry, name: string) {
    setRenamingEntry(null);
    if (!name || name === entry.name) return;
    const newPath = entry.path.split("/").slice(0, -1).concat(name).join("/");
    try {
      await renameFileItem(projectId, entry.path, newPath);
      await load(); // 自动刷新
      notify(`已重命名「${entry.name}」→「${name}」`);
    } catch (e) {
      notify(`重命名失败: ${(e as Error).message}`);
    }
  }

  // 阶段 7.9.5：内联重命名状态（Windows 式，同一时间仅一个条目在编辑）
  const [renamingEntry, setRenamingEntry] = useState<FileEntry | null>(null);

  async function handlePaste(targetPath: string) {
    if (!clipboard) return;
    const dest = targetPath ? `${targetPath}/${clipboard.path.split("/").pop()}` : clipboard.path.split("/").pop() || "";
    try {
      if (clipboard.op === "copy") {
        await copyFileItem(projectId, clipboard.path, dest);
      } else {
        await renameFileItem(projectId, clipboard.path, dest);
      }
      setClipboard(null);
      await load(); // 自动刷新（阶段 7.9.2：缓存禁用后必拿最新数据）
      notify(`粘贴成功：已${clipboard.op === "copy" ? "复制" : "剪切"}「${clipboard.path.split("/").pop()}」到 ${dest || "根目录"}`);
    } catch (e) {
      notify(`粘贴失败: ${(e as Error).message}`);
    }
  }

  async function handleDelete(entry: FileEntry) {
    // 阶段 7.9.2：明确提示删除系统真实文件（不可撤销）
    if (!await confirm(`确定删除「${entry.path}」？\n\n此操作将删除系统磁盘上的真实文件${entry.type === "directory" ? "/文件夹及其全部内容" : ""}，且不可恢复。`)) return;
    try {
      await deleteFileItem(projectId, entry.path);
      const inside = (path: string) => path === entry.path || path.startsWith(`${entry.path}/`);
      setActiveDir(current => inside(current) ? entry.path.split("/").slice(0, -1).join("/") : current);
      setExpanded(current => new Set([...current].filter(path => !inside(path))));
      setClipboard(current => current && inside(current.path) ? null : current);
      await load(); // 自动刷新
      notify(`已删除「${entry.name}」`);
    } catch (e) {
      notify(`删除失败: ${(e as Error).message}`);
    }
  }

  async function handleImport(entry: FileEntry) {
    try {
      await importDataFile(projectId, entry.path);
      onImported();
      notify("已开始导入到项目数据库");
    } catch (e) {
      notify(`导入失败: ${(e as Error).message}`);
    }
  }

  // 阶段 7.9：上传文件/文件夹到指定目录
  async function handleUpload(files: FileList | null, targetDir: string, preservePaths = false) {
    if (!files?.length || uploadInFlight.current) return;
    uploadInFlight.current = true;
    setUploading(true);
    setUploadResult(null);
    let ok = 0;
    const failures: {path: string; reason: string}[] = [];
    try {
      for (const f of Array.from(files)) {
        const relativePath = preservePaths ? f.webkitRelativePath : undefined;
        try {
          await uploadFile(projectId, targetDir, f, relativePath || undefined);
          ok++;
        } catch (e) {
          failures.push({path: relativePath || f.name, reason: (e as Error).message});
        }
      }
      await load();
      setUploadResult({ok, failures});
      if (failures.length === 0) {
        notify(`已上传 ${ok} 个文件`);
      } else {
        notify(`上传完成：成功 ${ok} 个，失败 ${failures.length} 个；失败详情见文件面板`);
      }
    } finally {
      setUploading(false);
      uploadInFlight.current = false;
    }
  }

  async function handleOpen(entry: FileEntry) {
    if (entry.type === "directory") return;
    try {
      // D3：按 viewer_type 分发（设计文档 §2.1 双击行为矩阵）
      const meta = await fileMeta(projectId, entry.path);
      const vt = meta.file.viewer_type;
      // office 双击直接调用本机程序打开（不进预览容器）
      if (vt === "docx" || vt === "xlsx" || vt === "xls" || vt === "ppt" || vt === "pptx") {
        await openExternalFile(projectId, entry.path);
        return;
      }
      // D7：pdf / 图片 / md / 文本类 → 新标签页预览（独立浏览器 tab，可自由切换）
      // 不再占用当前应用页（防止关闭预览误关应用主页）
      const url = `/?preview=1&project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(entry.path)}`;
      window.open(url, "_blank");
    } catch (e) {
      notify(`无法打开: ${(e as Error).message}`);
    }
  }

  // ---------- 渲染 ----------

  const renderRow = (entry: FileEntry, depth: number) => (
    <FileRow
      key={entry.path}
      entry={entry}
      depth={depth}
      expanded={expanded}
      onToggle={toggle}
      onPick={onPick}
      onContext={(e, entry) => { e.preventDefault(); setCtx({ x: e.clientX, y: e.clientY, entry }); if (entry.type === "directory") setActiveDir(entry.path); }}
      onDoubleClick={handleOpen}
      onDragRef={(e, entry) => {
        // 阶段 7.9.2：拖拽引用——拖出文件路径到输入框
        e.dataTransfer.setData("text/plain", `@${entry.type === "directory" ? "folder" : "file"}:${entry.path}`);
        e.dataTransfer.effectAllowed = "copy";
      }}
      renamingEntry={renamingEntry}
      onRenameCommit={commitRename}
      onRenameCancel={() => setRenamingEntry(null)}
      onRenameRequest={startRename}
      projectId={projectId}
      revision={revision}
      loadChildren={loadChildren}
    />
  );

  return (
    <div className={`drawer-anim absolute inset-y-0 right-0 z-20 flex w-80 flex-col border-l border-zinc-200 bg-white shadow-2xl ${closing ? "drawer-anim-out" : ""}`}>
      {/* 头部（阶段 7.9.9.1：两行布局——标题/按钮固定一行，路径独立一行，
          无论路径多长都不挤兑标题；此前路径与标题同行，长路径挤压导致换行） */}
      <div className="border-b border-zinc-200 px-3 py-2">
        <div className="flex items-center gap-2">
          <Folder size={14} className="text-blue-600" />
          <span className="text-sm font-medium text-zinc-700">项目目录</span>
          <div className="ml-auto flex items-center gap-0.5">
          {/* 阶段 7.9.2：集成「新建」「上传」两个纯图标下拉按钮（与刷新/关闭同尺寸） */}
          <div className="relative" ref={topMenuRef}>
            <button
              className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600"
              onClick={(e) => { e.stopPropagation(); setTopMenu(topMenu === "new" ? null : "new"); }}
              title="新建"
            >
              <Plus size={13} />
            </button>
            {topMenu === "new" && (
              <div className="absolute right-0 top-full z-50 mt-1 w-40 rounded-lg border border-zinc-200 bg-white py-1 shadow-xl">
                <button
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100"
                  onClick={(e) => { e.stopPropagation(); setTopMenu(null); handleCreate("file", activeDir); }}
                >
                  <FilePlus size={12} className="text-zinc-400" /> 新建文件
                </button>
                <button
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100"
                  onClick={(e) => { e.stopPropagation(); setTopMenu(null); handleCreate("directory", activeDir); }}
                >
                  <FolderPlus size={12} className="text-zinc-400" /> 新建文件夹
                </button>
              </div>
            )}
          </div>
          <div className="relative">
            <button
              className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 disabled:opacity-40"
              onClick={(e) => { e.stopPropagation(); setTopMenu(topMenu === "upload" ? null : "upload"); }}
              disabled={uploading}
              title="上传"
            >
              <Upload size={13} />
            </button>
            {topMenu === "upload" && (
              <div className="absolute right-0 top-full z-50 mt-1 w-40 rounded-lg border border-zinc-200 bg-white py-1 shadow-xl">
                <button
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100"
                  onClick={(e) => { e.stopPropagation(); setTopMenu(null); fileInputRef.current?.click(); }}
                >
                  <FileUp size={12} className="text-zinc-400" /> 上传文件
                </button>
                <button
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-zinc-700 hover:bg-zinc-100"
                  onClick={(e) => { e.stopPropagation(); setTopMenu(null); folderInputRef.current?.click(); }}
                >
                  <FolderUp size={12} className="text-zinc-400" /> 上传文件夹
                </button>
              </div>
            )}
          </div>
          <button className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600" onClick={() => load()} title="刷新">
            <RefreshCw size={13} />
          </button>
          <button className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600" onClick={onClose} title="关闭">
            <X size={14} />
          </button>
        </div>
        </div>
        {/* 第二行：工作目录路径（独立一行，min-w-0 保证 truncate 生效，不挤兑标题） */}
        <div className="mt-0.5 truncate pl-6 text-xs text-zinc-400" title={workDir}>
          {workDir}
        </div>
      </div>

      {/* 隐藏的文件/文件夹选择 input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => { handleUpload(e.target.files, activeDir); e.target.value = ""; }}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        className="hidden"
        /* @ts-expect-error webkitdirectory 非标准属性 */
        webkitdirectory=""
        directory=""
        onChange={(e) => { handleUpload(e.target.files, activeDir, true); e.target.value = ""; }}
      />

      {/* 列表 */}
      {uploadResult && uploadResult.failures.length > 0 && (
        <div role="status" className="border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <details open>
            <summary className="cursor-pointer">上传完成：成功 {uploadResult.ok} 个，失败 {uploadResult.failures.length} 个</summary>
            <ul className="mt-1 max-h-28 space-y-1 overflow-y-auto break-words">
              {uploadResult.failures.map((failure, index) => <li key={index}>{failure.path}：{failure.reason}</li>)}
            </ul>
          </details>
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-2">
        {Object.entries(warnings).map(([path, messages]) => messages.length > 0 && <div key={path} role="status" className="mb-2 break-words text-xs text-amber-700">{messages.join("；")}</div>)}
        {loading && <div className="p-3 text-xs text-zinc-400">加载中…</div>}
        {error && <div className="p-3 text-xs text-red-500">{error}</div>}
        {!loading && !error && tree.length === 0 && (
          <div className="p-3 text-xs text-zinc-400">目录为空（右键或顶部 + 新建）</div>
        )}
        <div className="space-y-0.5">{tree.map((e) => renderRow(e, 0))}</div>
        {treeNextCursor && (
          <button
            className="mt-2 w-full rounded border border-zinc-200 px-3 py-1.5 text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-40"
            disabled={loadingMoreRoot}
            onClick={() => void loadMoreRoot()}
          >
            {loadingMoreRoot
              ? "加载中…"
              : `加载更多（${tree.length}/${treeTotal}）`}
          </button>
        )}
      </div>

      {/* 右键菜单（阶段 7.9.2：动态翻转 + max-h 兜底） */}
      {ctx && (
        <div
          ref={ctxMenuRef}
          className="fixed z-50 max-h-[75vh] w-44 overflow-y-auto rounded-lg border border-zinc-200 bg-white py-1 shadow-xl"
          style={ctxPos ? { left: ctxPos.left, top: ctxPos.top } : { left: 8, top: 8 }}
        >
          <CtxItem icon={<Eye size={13} />} label="打开" onClick={() => { handleOpen(ctx.entry); setCtx(null); }} disabled={ctx.entry.type !== "file"} />
          <CtxItem icon={<Database size={13} />} label="添加到项目数据库" onClick={() => { handleImport(ctx.entry); setCtx(null); }} disabled={ctx.entry.type !== "file"} />
          {/* 阶段 7.9.2：引用——把文件/文件夹路径插入输入框（@file/@folder 语法，AI 可识别） */}
          <CtxItem icon={<LinkIcon />} label="引用" onClick={() => { onRefer(ctx.entry.type === "directory" ? `@folder:${ctx.entry.path}` : `@file:${ctx.entry.path}`); setCtx(null); notify(`已引用「${ctx.entry.path}」`); }} />
          <CtxItem icon={<Pencil size={13} />} label="重命名" onClick={() => { startRename(ctx.entry); }} />
          <div className="my-1 border-t border-zinc-100" />
          <CtxItem icon={<FilePlus size={13} />} label="新建文件" onClick={() => { handleCreate("file", ctx.entry.type === "directory" ? ctx.entry.path : ""); setCtx(null); }} />
          <CtxItem icon={<FolderPlus size={13} />} label="新建文件夹" onClick={() => { handleCreate("directory", ctx.entry.type === "directory" ? ctx.entry.path : ""); setCtx(null); }} />
          <div className="my-1 border-t border-zinc-100" />
          {/* 阶段 7.9：上传文件/文件夹到当前目录 */}
          <CtxItem icon={<Upload size={13} />} label="上传文件到此" onClick={() => { setActiveDir(ctx.entry.type === "directory" ? ctx.entry.path : ""); setCtx(null); setTimeout(() => fileInputRef.current?.click(), 0); }} disabled={ctx.entry.type !== "directory"} />
          <CtxItem icon={<FolderPlus size={13} />} label="上传文件夹到此" onClick={() => { setActiveDir(ctx.entry.type === "directory" ? ctx.entry.path : ""); setCtx(null); setTimeout(() => folderInputRef.current?.click(), 0); }} disabled={ctx.entry.type !== "directory"} />
          <div className="my-1 border-t border-zinc-100" />
          <CtxItem icon={<Copy size={13} />} label="复制" onClick={() => { setClipboard({ op: "copy", path: ctx.entry.path }); setCtx(null); notify(`已复制「${ctx.entry.name}」，到目标位置右键「粘贴到此处」`); }} />
          <CtxItem icon={<Scissors size={13} />} label="剪切" onClick={() => { setClipboard({ op: "cut", path: ctx.entry.path }); setCtx(null); notify(`已剪切「${ctx.entry.name}」，到目标位置右键「粘贴到此处」`); }} />
          <CtxItem icon={<Clipboard size={13} />} label="粘贴到此处" onClick={() => { handlePaste(ctx.entry.type === "directory" ? ctx.entry.path : ""); setCtx(null); }} disabled={!clipboard} />
          <div className="my-1 border-t border-zinc-100" />
          <CtxItem icon={<Trash2 size={13} />} label="删除" danger onClick={() => { handleDelete(ctx.entry); setCtx(null); }} />
        </div>
      )}

      {/* 输入弹窗 */}
      {prompt && (
        <PromptDialog
          title={prompt.title}
          placeholder={prompt.placeholder}
          onSubmit={async (v) => { await prompt.onSubmit(v); setPrompt(null); }}
          onCancel={() => setPrompt(null)}
        />
      )}
    </div>
  );
}

// ---------- 子组件 ----------

function CtxItem({ icon, label, onClick, danger, disabled }: {
  icon: React.ReactNode; label: string; onClick: () => void; danger?: boolean; disabled?: boolean;
}) {
  return (
    <button
      className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm ${
        disabled ? "cursor-not-allowed text-zinc-300" : danger ? "text-red-600 hover:bg-red-50" : "text-zinc-700 hover:bg-zinc-100"
      }`}
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

function PromptDialog({ title, placeholder, onSubmit, onCancel }: {
  title: string; placeholder: string; onSubmit: (v: string) => Promise<void>; onCancel: () => void;
}) {
  const [val, setVal] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  const cancel = () => { if (!submitting.current) onCancel(); };
  async function submit() {
    if (submitting.current) return;
    if (!val.trim()) { setError("请输入名称"); return; }
    submitting.current = true;
    setPending(true);
    setError("");
    try { await onSubmit(val.trim()); }
    catch (error) { setError((error as Error).message); }
    finally { submitting.current = false; setPending(false); }
  }
  return (
    <Dialog title={title} onClose={cancel} className="max-w-sm">
      <form className="p-6 pt-12" onSubmit={e => { e.preventDefault(); void submit(); }}>
        <h3 className="mb-3 text-sm font-medium text-zinc-800">{title}</h3>
        <input
          autoFocus
          aria-label={title}
          aria-invalid={!!error}
          disabled={pending}
          value={val}
          onChange={(e) => setVal(e.target.value)}
          placeholder={placeholder}
          className="w-full rounded-lg border border-zinc-300 px-3 py-1.5 text-sm text-zinc-800 outline-none focus:border-blue-500"
        />
        {error && <p role="alert" className="mt-2 text-xs text-red-600">{error}</p>}
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" disabled={pending} className="rounded px-3 py-1 text-xs text-zinc-600 hover:bg-zinc-100" onClick={cancel}>取消</button>
          <button type="submit" disabled={pending} className="rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-500 disabled:opacity-40">{pending ? "创建中…" : "确定"}</button>
        </div>
      </form>
    </Dialog>
  );
}

function FileRow({
  entry, depth, expanded, onToggle, onPick, onContext, onDoubleClick, onDragRef, projectId, revision, loadChildren,
  renamingEntry, onRenameCommit, onRenameCancel, onRenameRequest,
}: {
  entry: FileEntry;
  depth: number;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  onPick: (path: string) => void;
  onContext: (e: React.MouseEvent, entry: FileEntry) => void;
  onDoubleClick: (entry: FileEntry) => void;
  onDragRef: (e: React.DragEvent, entry: FileEntry) => void;
  projectId: string;
  revision: number;
  loadChildren: (path: string, offset?: number) => Promise<FileTreeResponse>;
  renamingEntry: FileEntry | null;
  onRenameCommit: (entry: FileEntry, name: string) => void;
  onRenameCancel: () => void;
  onRenameRequest: (entry: FileEntry) => void;
}) {
  const [children, setChildren] = useState<FileEntry[]>([]);
  const [childrenTotal, setChildrenTotal] = useState(0);
  const [nextCursor, setNextCursor] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState("");
  // 阶段 7.9.5：内联重命名本地状态
  const [val, setVal] = useState(entry.name);
  const doneRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const isOpen = expanded.has(entry.path);
  const isDir = entry.type === "directory";
  const renaming = renamingEntry?.path === entry.path;

  // 进入重命名：重置输入并聚焦全选
  useEffect(() => {
    if (renaming) {
      setVal(entry.name);
      doneRef.current = false;
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [renaming, entry.name]);

  const commit = () => {
    if (doneRef.current) return;
    doneRef.current = true;
    onRenameCommit(entry, val.trim());
  };
  const cancel = () => {
    if (doneRef.current) return;
    doneRef.current = true;
    onRenameCancel();
  };

  useEffect(() => {
    if (!isOpen || !isDir) return;
    let active = true;
    setLoaded(false);
    setLoadError("");
    loadChildren(entry.path).then(page => {
      if (active) {
        setChildren(page.entries || []);
        setChildrenTotal(page.total || 0);
        setNextCursor(page.next_cursor || "");
        setLoaded(true);
      }
    }).catch((error: Error) => {
      if (active) { setChildren([]); setLoadError(error.message); }
    });
    return () => { active = false; };
  }, [isOpen, isDir, entry.path, loadChildren, revision]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await loadChildren(entry.path, Number(nextCursor));
      setChildren(previous => [...previous, ...(page.entries || [])]);
      setChildrenTotal(page.total || childrenTotal);
      setNextCursor(page.next_cursor || "");
    } finally {
      setLoadingMore(false);
    }
  }, [entry.path, loadChildren, nextCursor, childrenTotal, loadingMore]);

  return (
    <>
      <button
        className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-sm text-zinc-700 hover:bg-zinc-100"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => { if (isDir) onToggle(entry.path); }}
        onKeyDown={(e) => { if (e.key === "F2") { e.preventDefault(); onRenameRequest(entry); } }} // 阶段 7.9.5：F2 内联重命名
        onContextMenu={(e) => onContext(e, entry)}
        onDoubleClick={() => onDoubleClick(entry)}
        draggable
        onDragStart={(e) => onDragRef(e, entry)}
        title={entry.path}
      >
        {isDir ? (
          isOpen ? <ChevronDown size={12} className="shrink-0 text-zinc-400" /> : <ChevronRight size={12} className="shrink-0 text-zinc-400" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        {isDir ? <Folder size={13} className="shrink-0 text-amber-500" /> : <File size={13} className="shrink-0 text-zinc-400" />}
        {renaming ? (
          /* 阶段 7.9.5：Windows 式内联编辑——名称原位变输入框（Enter/blur 确认、Esc 取消） */
          <input
            ref={inputRef}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") commit();
              else if (e.key === "Escape") cancel();
            }}
            onBlur={commit}
            onDragStart={(e) => e.stopPropagation()}
            className="min-w-0 flex-1 rounded-lg border border-blue-500 px-1 py-0.5 text-sm text-zinc-800 outline-none"
          />
        ) : (
          <span className="truncate">{entry.name}</span>
        )}
      </button>
      {isOpen && isDir && (
        <div className="space-y-0.5">
          {loaded && children.map((c) => (
            <FileRow
              key={c.path} entry={c} depth={depth + 1} expanded={expanded}
              onToggle={onToggle} onPick={onPick} onContext={onContext}
              onDoubleClick={onDoubleClick} onDragRef={onDragRef}
              projectId={projectId} revision={revision} loadChildren={loadChildren}
              renamingEntry={renamingEntry} onRenameCommit={onRenameCommit}
              onRenameCancel={onRenameCancel} onRenameRequest={onRenameRequest}
            />
          ))}
          {loadError && <div role="alert" className="px-3 py-1 text-xs text-red-500">{loadError}，请刷新后重试</div>}
          {!loaded && !loadError && <div className="px-3 py-1 text-xs text-zinc-400">加载中…</div>}
          {nextCursor && (
            <button
              className="mx-2 mt-1 w-[calc(100%-1rem)] rounded border border-zinc-200 px-3 py-1 text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-40"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore
                ? "加载中…"
                : `加载更多（${children.length}/${childrenTotal}）`}
            </button>
          )}
          {loaded && children.length === 0 && (
            <div className="px-3 py-1 text-xs text-zinc-400" style={{ paddingLeft: `${8 + (depth + 1) * 14}px` }}>
              空目录
            </div>
          )}
        </div>
      )}
    </>
  );
}

/** 引用图标（阶段 7.9.2） */
function LinkIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}
