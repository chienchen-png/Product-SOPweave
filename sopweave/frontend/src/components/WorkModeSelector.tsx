/** WorkModeSelector — 工作模式切换（阶段 G4）。
 *
 * 位于 composer 工具行（模型徽标右侧、项目目录左侧），与模型切换同构：
 *   人工审批（manual，默认）：写入/数据修改/命令行 → 弹确认
 *   全自动（auto）：项目内写入自动执行；本地命令和 SOP 强制审批仍需确认
 *
 * 视觉：安全模式绿色徽标；全自动红色警示。下拉含说明文案。
 */

import { useState } from "react";
import { ChevronDown, ShieldCheck, Zap } from "lucide-react";

export type WorkMode = "manual" | "auto";

export default function WorkModeSelector({
  workMode,
  onChange,
}: {
  workMode: WorkMode;
  onChange: (mode: WorkMode) => void;
}) {
  const [open, setOpen] = useState(false);

  const manual = workMode !== "auto";

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        title={
          manual
            ? "工作模式：人工审批（写入、数据修改与本地命令需确认）"
            : "工作模式：全自动（项目内写入自动；本地命令与 SOP 强制审批仍需确认）"
        }
        className={`flex h-7 items-center gap-1.5 rounded-md px-2 text-xs transition-colors ${
          manual
            ? "text-emerald-600 hover:bg-emerald-50"
            : "text-red-600 hover:bg-red-50"
        }`}
      >
        {manual ? <ShieldCheck size={13} /> : <Zap size={13} />}
        <span className="max-w-24 truncate font-medium">
          {manual ? "人工审批" : "全自动"}
        </span>
        <ChevronDown size={11} className="text-zinc-400" />
      </button>

      {open && (
        <>
          {/* 点击外部关闭 */}
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          {/* 向上弹出（对齐模型选择器） */}
          <div className="absolute bottom-full left-0 z-30 mb-1 w-60 overflow-hidden rounded-lg border border-zinc-200 bg-white p-1 shadow-xl">
            {/* 人工审批 */}
            <button
              onClick={() => { onChange("manual"); setOpen(false); }}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-zinc-100 ${
                manual ? "bg-emerald-50" : ""
              }`}
            >
              <ShieldCheck size={14} className={`shrink-0 ${manual ? "text-emerald-600" : "text-zinc-400"}`} />
              <span className="flex-1 truncate">
                <span className={`text-xs font-medium ${manual ? "text-emerald-700" : "text-zinc-600"}`}>
                  人工审批
                  {manual && <span className="ml-1.5 rounded bg-emerald-50 px-1.5 text-xs text-emerald-600">当前</span>}
                </span>
                <span className="ml-2 text-xs text-zinc-400">写入、数据修改与本地命令需确认</span>
              </span>
            </button>

            {/* 全自动 */}
            <button
              onClick={() => { onChange("auto"); setOpen(false); }}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-zinc-100 ${
                !manual ? "bg-red-50" : ""
              }`}
            >
              <Zap size={14} className={`shrink-0 ${!manual ? "text-red-500" : "text-zinc-400"}`} />
              <span className="flex-1 truncate">
                <span className={`text-xs font-medium ${!manual ? "text-red-600" : "text-zinc-600"}`}>
                  全自动
                  {!manual && <span className="ml-1.5 rounded bg-red-50 px-1.5 text-xs text-red-600">当前</span>}
                </span>
                <span className="ml-2 text-xs text-zinc-400">项目内写入自动；本地命令仍需确认</span>
              </span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}
