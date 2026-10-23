/** DirectoryPicker — Windows 11 风格自研目录选择器（阶段 7.9.8）。
 *
 * 目标：几乎看不出是自研、零学习成本——交互完全对齐 Windows 系统文件夹对话框：
 *  - 左侧「此电脑」侧栏：磁盘驱动器 + 常用位置（桌面/文档/下载/图片/音乐/视频）
 *  - 顶部：后退 / 前进 / 向上 + 面包屑导航
 *  - 主体：目录网格（单击选中、双击进入，与 Windows 一致）
 *  - 底部：当前路径 + 取消 / 确定（确定用「选中项」或当前目录）
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Folder,
  File,
  ChevronRight,
  ArrowUp,
  ArrowLeft,
  ArrowRight,
  HardDrive,
  Loader2,
  X,
  Check,
  Home,
  FileText,
  Download,
  Image as ImageIcon,
  Music,
  Video as VideoIcon,
  Monitor,
} from "lucide-react";
import {
  browseDirectory,
  browseDrives,
  type BrowseTreeResult,
} from "../lib/api";
import Dialog from "./ui/Dialog";

interface SideItem {
  name: string;
  path: string;
  kind: "drive" | "location";
  icon: React.ReactNode;
}

const LOCATION_ICONS: Record<string, React.ReactNode> = {
  桌面: <Home size={15} className="text-sky-600" />,
  文档: <FileText size={15} className="text-sky-600" />,
  下载: <Download size={15} className="text-sky-600" />,
  图片: <ImageIcon size={15} className="text-sky-600" />,
  音乐: <Music size={15} className="text-sky-600" />,
  视频: <VideoIcon size={15} className="text-sky-600" />,
};

export default function DirectoryPicker({
  onSelect,
  onCancel,
}: {
  onSelect: (path: string) => void;
  onCancel: () => void;
}) {
  const [dir, setDir] = useState<BrowseTreeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [side, setSide] = useState<SideItem[]>([]);
  // 单击选中（Windows 交互：选中后再点确定；双击直接进入）
  const [selected, setSelected] = useState<string | null>(null);
  // 导航历史
  const [history, setHistory] = useState<{ stack: string[]; fwd: string[] }>({
    stack: [],
    fwd: [],
  });
  const currentDir = useRef<BrowseTreeResult | null>(null);
  const requestSequence = useRef(0);

  const load = useCallback(
    async (
      path?: string,
      navigation: "initial" | "push" | "back" | "forward" = "initial",
    ) => {
      const sequence = ++requestSequence.current;
      const previous = currentDir.current?.path;
      setLoading(true);
      setError("");
      setSelected(null);
      try {
        const result = await browseDirectory(path);
        if (sequence !== requestSequence.current) return;
        if (previous && previous !== result.path) {
          setHistory((h) => {
            if (navigation === "back")
              return { stack: h.stack.slice(0, -1), fwd: [previous, ...h.fwd] };
            if (navigation === "forward")
              return { stack: [...h.stack, previous], fwd: h.fwd.slice(1) };
            if (navigation === "push")
              return { stack: [...h.stack, previous], fwd: [] };
            return h;
          });
        }
        currentDir.current = result;
        setDir(result);
      } catch (e) {
        if (sequence === requestSequence.current)
          setError((e as Error).message);
      } finally {
        if (sequence === requestSequence.current) setLoading(false);
      }
    },
    [],
  );

  // 初始：先取 drives（含 start=真实桌面），再用 start 打开（否则主目录）
  useEffect(() => {
    let active = true;
    browseDrives()
      .then((d) => {
        if (!active) return;
        const items: SideItem[] = [
          ...d.drives.map((x) => ({
            name: x.name,
            path: x.path,
            kind: "drive" as const,
            icon: <HardDrive size={15} className="text-zinc-500" />,
          })),
          ...d.locations.map((x) => ({
            name: x.name,
            path: x.path,
            kind: "location" as const,
            icon: LOCATION_ICONS[x.name] || (
              <Folder size={15} className="text-zinc-500" />
            ),
          })),
        ];
        setSide(items);
        // 阶段 7.9.8.3：初始目录用注册表真实桌面（兼容 OneDrive 重定向）
        load(d.start || undefined);
      })
      .catch(() => {
        if (active) void load(undefined);
      });
    return () => {
      active = false;
      requestSequence.current++;
    };
  }, [load]);

  // 进入目录（推历史）
  function enter(path: string) {
    if (!dir || loading) return;
    void load(path, "push");
  }

  function goBack() {
    if (loading || history.stack.length === 0) return;
    const prev = history.stack[history.stack.length - 1];
    void load(prev, "back");
  }
  function goForward() {
    if (loading || history.fwd.length === 0) return;
    const next = history.fwd[0];
    void load(next, "forward");
  }
  function goUp() {
    if (!dir?.parent) return;
    enter(dir.parent);
  }
  function goSide(item: SideItem) {
    enter(item.path);
  }
  // 面包屑跳转（层级从 0 开始，0=根盘符）
  function goCrumb(index: number, parts: { label: string; path: string }[]) {
    enter(parts[index].path);
  }

  // 面包屑：解析当前绝对路径为层级
  const crumbs = (() => {
    if (!dir) return [];
    const full = dir.path.replace(/\//g, "\\");
    const m = full.match(/^([A-Za-z]:\\)?(.*)$/);
    const drivePart = m?.[1] || "";
    const rest = (m?.[2] || "").split("\\").filter(Boolean);
    const parts: { label: string; path: string }[] = [];
    if (drivePart) parts.push({ label: drivePart, path: drivePart });
    let acc = drivePart;
    for (const seg of rest) {
      acc += seg + "\\";
      parts.push({ label: seg, path: acc });
    }
    return parts;
  })();

  const confirmPath = selected || dir?.path || "";

  return (
    <Dialog
      title="选择工作目录"
      onClose={onCancel}
      showClose={false}
      className="h-[30rem] max-h-[calc(100vh-5rem)] max-w-2xl"
    >
      {/* 顶栏：导航 + 面包屑（Windows 风格） */}
      <div className="flex items-center gap-0.5 border-b border-zinc-200 bg-[#f6f6f6] px-2 py-1.5">
        <button
          onClick={goBack}
          disabled={loading || history.stack.length === 0}
          title="后退"
          className="rounded p-1.5 text-zinc-600 hover:bg-zinc-200 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ArrowLeft size={15} />
        </button>
        <button
          onClick={goForward}
          disabled={loading || history.fwd.length === 0}
          title="前进"
          className="rounded p-1.5 text-zinc-600 hover:bg-zinc-200 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ArrowRight size={15} />
        </button>
        <button
          onClick={goUp}
          disabled={loading || !dir?.parent}
          title="向上一级"
          className="rounded p-1.5 text-zinc-600 hover:bg-zinc-200 disabled:opacity-30 disabled:hover:bg-transparent"
        >
          <ArrowUp size={15} />
        </button>
        <div className="mx-1 h-5 w-px bg-zinc-300" />
        {/* 面包屑 */}
        <div className="flex min-w-0 flex-1 items-center overflow-hidden rounded bg-white px-1.5 py-1 text-xs text-zinc-700 ring-1 ring-zinc-200">
          <Monitor size={13} className="mr-1 shrink-0 text-zinc-400" />
          {crumbs.length === 0 && (
            <span className="truncate text-zinc-500">{dir?.path || "…"}</span>
          )}
          {crumbs.map((c, i) => (
            <span key={i} className="flex shrink-0 items-center">
              {i > 0 && (
                <ChevronRight size={11} className="mx-0.5 text-zinc-300" />
              )}
              <button
                onClick={() => goCrumb(i, crumbs)}
                disabled={loading}
                className={`max-w-28 truncate rounded px-1 py-0.5 hover:bg-zinc-200 ${
                  i === crumbs.length - 1
                    ? "font-medium text-zinc-900"
                    : "text-zinc-600"
                }`}
                title={c.path}
              >
                {c.label}
              </button>
            </span>
          ))}
        </div>
        <button
          onClick={onCancel}
          title="关闭"
          className="ml-1 rounded p-1.5 text-zinc-500 hover:bg-zinc-200"
        >
          <X size={15} />
        </button>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* 左侧栏：此电脑（驱动器 + 常用位置） */}
        <div className="w-44 shrink-0 overflow-y-auto border-r border-zinc-200 bg-[#f6f6f6] py-1">
          <div className="px-2 pb-0.5 pt-1 text-xs font-medium text-zinc-500">
            此电脑
          </div>
          {side.map((s) => (
            <button
              key={s.path}
              disabled={loading}
              onClick={() => goSide(s)}
              className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-xs ${
                dir?.path === s.path
                  ? "bg-[#cce8ff] text-zinc-900"
                  : "text-zinc-700 hover:bg-zinc-200/70"
              }`}
              title={s.path}
            >
              {s.icon}
              <span className="truncate">{s.name}</span>
            </button>
          ))}
          {side.length === 0 && (
            <div className="px-2 py-1 text-xs text-zinc-400">无可用位置</div>
          )}
        </div>

        {/* 主体：目录网格 */}
        <div className="flex-1 overflow-y-auto bg-white p-3">
          {loading && (
            <div className="flex h-full items-center justify-center gap-2 text-xs text-zinc-400">
              <Loader2 size={14} className="animate-spin" /> 加载中…
            </div>
          )}
          {error && (
            <div role="alert" className="p-3 text-xs text-red-500">
              {error}
            </div>
          )}
          {!loading && !error && dir && dir.entries.length === 0 && (
            <div className="flex h-full items-center justify-center text-xs text-zinc-400">
              此文件夹为空
            </div>
          )}
          {!loading && !error && dir && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(92px,1fr))] gap-1">
              {dir.entries.map((e) => {
                const isDir = e.type !== "file";
                return (
                  <button
                    key={e.path}
                    onClick={() => {
                      if (isDir) setSelected(e.path);
                    }}
                    onDoubleClick={() => {
                      if (isDir) enter(e.path);
                    }}
                    title={isDir ? e.name : `${e.name}（文件，仅显示）`}
                    className={`flex flex-col items-center gap-1 rounded-lg border px-1 py-2 text-center ${
                      isDir
                        ? selected === e.path
                          ? "border-[#0078d4] bg-[#e5f1fb]"
                          : "border-transparent hover:border-zinc-200 hover:bg-zinc-50"
                        : "border-transparent opacity-60" // 文件灰显不可选
                    }`}
                  >
                    {isDir ? (
                      <Folder
                        size={30}
                        className="shrink-0 text-amber-400"
                        fill="#fcd34d"
                        fillOpacity={0.35}
                      />
                    ) : (
                      <File size={30} className="shrink-0 text-zinc-300" />
                    )}
                    <span
                      className={`line-clamp-2 max-w-full break-all text-xs leading-tight ${isDir ? "text-zinc-700" : "text-zinc-400"}`}
                    >
                      {e.name}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* 底栏：路径 + 确定/取消 */}
      <div className="flex items-center gap-2 border-t border-zinc-200 bg-[#f6f6f6] px-3 py-2.5">
        <span
          className="min-w-0 flex-1 truncate rounded bg-white px-2 py-1 font-mono text-xs text-zinc-500 ring-1 ring-zinc-200"
          title={confirmPath}
        >
          {confirmPath}
        </span>
        <button
          onClick={onCancel}
          className="rounded px-3.5 py-1.5 text-xs text-zinc-700 hover:bg-zinc-200"
        >
          取消
        </button>
        <button
          onClick={() => onSelect(confirmPath)}
          disabled={!confirmPath || loading || !!error}
          className="flex items-center gap-1 rounded bg-[#0067c0] px-3.5 py-1.5 text-xs font-medium text-white hover:bg-[#005a9e] disabled:opacity-40"
        >
          <Check size={13} /> 确定
        </button>
      </div>
    </Dialog>
  );
}
