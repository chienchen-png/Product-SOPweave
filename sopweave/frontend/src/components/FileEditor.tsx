/** FileEditor — 文件预览/编辑容器（阶段 4 → D3 升级，2026-08-24）。
 *
 * 按 viewer_type 分发的统一预览容器（设计文档 §2.1 双击行为矩阵）：
 *  - markdown → MarkdownEditor（Vditor 渲染 + 编辑 + 保存；导出按钮 D4 接入）
 *  - pdf      → iframe 加载 raw 二进制流（浏览器内置 PDF 查看器，零依赖）
 *  - image    → <img> 浏览器预览
 *  - text/code→ textarea 编辑保存（原逻辑保留）
 *  - office   → 提示 + 「用系统打开」按钮（兜底；正常流程在 FileDrawer 已分流
 *               为双击直接 open-external，不会进入本容器）
 *
 * 加载：先取 fileMeta 得 viewer_type，再按需 readFileContent（仅文本类）。
 */

import { useEffect, useRef, useState } from "react";
import { Save, FileCode, ExternalLink } from "lucide-react";
import {
  readFileContent, writeFileContent, fileMeta, openExternalFile, rawFileUrl,
  exportDocxFile, downloadBlob, fileResourceBase,
} from "../lib/api";
import MarkdownEditor from "./MarkdownEditor";
import Dialog from './ui/Dialog';
import { readMigratedStorage } from '../lib/brand';

export default function FileEditor({
  projectId,
  filePath,
  onClose,
  onSaved,
  embedded = false,
  resourceBaseUrl,
}: {
  projectId: string;
  filePath: string;
  onClose: () => void;
  onSaved: () => void;
  /** 内嵌模式：显示在应用内容区（不用全屏 fixed 覆盖），隐藏「关闭」按钮 */
  embedded?: boolean;
  /** Override when report image paths are relative to the project root. */
  resourceBaseUrl?: string;
}) {
  const [viewerType, setViewerType] = useState<string>("");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [version, setVersion] = useState("");
  const [actionError,setActionError] = useState('');
  const [diskPreview,setDiskPreview]=useState<{content:string;version:string}|null>(null);
  const [editorRevision,setEditorRevision]=useState(0);
  const currentContent=useRef('');
  const cacheKey=`sopweave.file-draft.v1:${projectId}:${filePath}`;
  const scope=useRef(cacheKey);scope.current=cacheKey;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setActionError('');setDiskPreview(null);
    setViewerType("");
    setContent("");
    setDirty(false);
    setVersion("");
    (async () => {
      try {
        const meta = await fileMeta(projectId, filePath);
        if (cancelled) return;
        const vt = meta.file.viewer_type;
        setViewerType(vt);
        // 文本类（text/code/markdown）→ 读取内容供编辑器；二进制类无需读取
        if (vt === "text" || vt === "code" || vt === "markdown") {
          const d = await readFileContent(projectId, filePath);
          if (cancelled) return;
          let restored:{content:string;version:string}|null=null;
          try{const value=JSON.parse(readMigratedStorage(cacheKey)||'null');if(typeof value?.content==='string'&&typeof value?.version==='string')restored=value;}catch{}
          const text=restored?.content??d.content;
          setContent(text);currentContent.current=text;
          setVersion(restored?.version??d.version);
          setDirty(!!restored);
          if(restored&&restored.version!==d.version)setActionError('磁盘版本已变化；本地编辑已恢复，保存前请比较文件内容。');
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [projectId, filePath]);

  const isText = viewerType === "text" || viewerType === "code";
  const isMarkdown = viewerType === "markdown";
  const isOffice = viewerType === "docx" || viewerType === "xlsx" || viewerType === "xls"
    || viewerType === "ppt" || viewerType === "pptx";
  const isBinaryPreview = viewerType === "pdf" || viewerType === "image";

  // md 上传按钮：指向 filePath 所在目录
  const uploadUrl = isMarkdown
    ? (() => {
        const dir = filePath.includes("/") ? filePath.slice(0, filePath.lastIndexOf("/")) : "";
        return `/api/files/upload?project_id=${encodeURIComponent(projectId)}&path=${encodeURIComponent(dir)}`;
      })()
    : undefined;

  async function handleSave() {
    if(saving)return;
    setSaving(true);
    setActionError('');
    const sent=currentContent.current;
    try {
      const saved = await writeFileContent(projectId, filePath, sent, version);
      if(scope.current!==cacheKey)return;
      setVersion(saved.version);
      const changed=currentContent.current!==sent;
      setDirty(changed);
      try{if(changed)window.localStorage.setItem(cacheKey,JSON.stringify({content:currentContent.current,version:saved.version}));else window.localStorage.removeItem(cacheKey);}catch{}
      onSaved();
    } catch (e) {
      if(scope.current===cacheKey)setActionError(`保存失败: ${(e as Error).message}`);
    } finally {
      if(scope.current===cacheKey)setSaving(false);
    }
  }

  function changeContent(value:string){
    if(value===currentContent.current)return;
    currentContent.current=value;setContent(value);setDirty(true);
    try{window.localStorage.setItem(cacheKey,JSON.stringify({content:value,version}));}
    catch{setActionError('本地备份失败，请先保存报告再离开页面。');}
  }

  async function handleOpenExternal() {
    try {
      await openExternalFile(projectId, filePath);
    } catch (e) {
      setActionError(`打开失败: ${(e as Error).message}`);
    }
  }

  // D4：导出 Word（未保存的编辑内容直接导出）
  async function handleExportWord(md: string) {
    try {
      const blob = await exportDocxFile(projectId, filePath, md);
      const stem = filePath.split("/").pop()?.replace(/\.(md|markdown)$/i, "") || "document";
      downloadBlob(blob, `${stem}.docx`);
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  return (
    <div className={`${embedded ? "flex h-full min-h-0 flex-1 flex-col bg-zinc-50" : "fixed inset-0 z-40 flex flex-col bg-zinc-50"} print:static print:block print:h-auto print:overflow-visible print:bg-white`}>
      {/* 头部 */}
      <div className="flex items-center gap-2 border-b border-zinc-200 bg-white px-4 py-2 print:hidden">
        <FileCode size={14} className="text-blue-600" />
        <span className="truncate text-sm font-medium text-zinc-700">{filePath}</span>
        {dirty && <span className="text-xs text-amber-600">● 未保存</span>}
        <div className="ml-auto flex items-center gap-1.5">
          {(isMarkdown || isText) && (
            <button
              onClick={handleSave}
              disabled={!dirty || saving}
              className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500 disabled:opacity-40"
            >
              <Save size={12} />
              {saving ? "保存中…" : "保存"}
            </button>
          )}
          {isOffice && (
            <button
              onClick={() => void handleOpenExternal()}
              className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500"
            >
              <ExternalLink size={12} />
              用系统打开
            </button>
          )}
          {!embedded && (
            <button className="rounded-md px-2 py-1.5 text-xs text-zinc-500 hover:bg-zinc-100" onClick={onClose}>
              关闭
            </button>
          )}
        </div>
      </div>

      {/* 内容 */}
      {actionError&&<div className="border-b bg-red-50 px-4 py-2 text-sm text-red-700 print:hidden"><p role="alert">{actionError}</p>{dirty&&<button className="mt-2 underline" onClick={()=>void readFileContent(projectId,filePath).then(setDiskPreview).catch(cause=>setActionError(cause.message))}>查看磁盘版本并比较</button>}</div>}
      {diskPreview&&<Dialog title="比较磁盘版本" onClose={()=>setDiskPreview(null)} className="max-w-4xl"><div className="min-h-0 space-y-4 overflow-y-auto p-6 pt-12"><p className="text-sm text-zinc-600">当前编辑仍保留在原窗口。可复制下方内容合并，或采用磁盘版本替换当前编辑。</p><pre className="max-h-[55vh] overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-50 p-4 text-sm">{diskPreview.content}</pre><button className="ui-button" onClick={()=>{
        setContent(diskPreview.content);currentContent.current=diskPreview.content;setVersion(diskPreview.version);setDirty(false);setEditorRevision(value=>value+1);setActionError('');setDiskPreview(null);try{window.localStorage.removeItem(cacheKey);}catch{}
      }}>采用磁盘版本，替换当前编辑</button></div></Dialog>}
      <div className="flex-1 overflow-auto p-4 print:block print:h-auto print:overflow-visible print:p-0">
        {loading && <div className="text-sm text-zinc-400">加载中…</div>}
        {error && <div className="text-sm text-red-500">{error}</div>}
        {!loading && !error && (
          <>
            {/* markdown → Vditor 编辑器（渲染 + 编辑 + 保存 + 上传图片） */}
            {isMarkdown && (
              <MarkdownEditor
                key={`${filePath}:${editorRevision}`}
                initialValue={content}
                onChange={changeContent}
                uploadUrl={uploadUrl}
                showExport
                onExportWord={(md) => void handleExportWord(md)}
                resourceBaseUrl={resourceBaseUrl ?? fileResourceBase(projectId, filePath.includes("/") ? filePath.slice(0, filePath.lastIndexOf("/")) : "")}
                className="h-full print:h-auto"
              />
            )}
            {/* pdf → iframe 浏览器预览 */}
            {viewerType === "pdf" && (
              <iframe
                src={rawFileUrl(projectId, filePath, true)}
                title={filePath}
                className="h-full w-full rounded-lg border border-zinc-200 bg-white"
              />
            )}
            {/* image → 浏览器预览 */}
            {viewerType === "image" && (
              <div className="flex h-full items-center justify-center">
                <img
                  src={rawFileUrl(projectId, filePath, true)}
                  alt={filePath}
                  className="max-h-full max-w-full rounded-lg border border-zinc-200 object-contain shadow-sm"
                />
              </div>
            )}
            {/* office 兜底（正常双击已在 FileDrawer 分流外部打开） */}
            {isOffice && (
              <div className="mt-10 text-center text-sm text-zinc-400">
                该文件为 Office 文档，建议用本机程序打开
                <br />
                <span className="text-xs">（点击右上角「用系统打开」）</span>
              </div>
            )}
            {/* 其他二进制（不应出现） */}
            {viewerType !== "" && !isText && !isMarkdown && !isOffice && !isBinaryPreview && (
              <div className="mt-10 text-center text-sm text-zinc-400">
                该文件类型暂不支持预览
              </div>
            )}
            {/* text / code → textarea 编辑 */}
            {isText && (
              <textarea
                value={content}
                onChange={(e) => changeContent(e.target.value)}
                spellCheck={false}
                className="h-full w-full resize-none rounded-lg border border-zinc-200 bg-white p-3 font-mono text-sm leading-relaxed text-zinc-800 outline-none focus:border-blue-400"
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
