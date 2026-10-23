import { History, RefreshCw, FileText, Trash2, BookOpen } from "lucide-react";
export interface ReportHistoryItem {
  id: string;            // 唯一标识（run_id）
  runId: string;
  rel_path: string;      // 报告 md 相对项目根（含 财务分析报告/财务分析报告N/xxx.md）
  filename: string;      // 如 财务分析报告1.md
  goal: string;          // 本次分析诉求（标题展示）
  createdAt: string;     // 生成时间（ISO）
}

export default function ReportHistoryPanel({
  items: reportHistory, onRefresh: handleRefreshReports, onDelete: handleDeleteReport, onOpen: openReportInNewTab, error='',
}: {
  items: ReportHistoryItem[];
  onRefresh: () => void;
  onDelete: (id: string) => void;
  onOpen: (item: ReportHistoryItem) => void;
  error?: string;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-5 pb-4">
      {error&&<p role="alert" className="mb-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
      <div className="mx-auto max-w-3xl space-y-4">
        <div className="flex items-center gap-2">
          <History size={16} className="text-zinc-400" />
          <h2 className="text-base font-semibold text-zinc-800">历史记录</h2>
          <span className="text-xs text-zinc-400">（{reportHistory.length} 份分析报告 · 点击在新标签页打开）</span>
          <button
            onClick={handleRefreshReports}
            title="刷新历史"
            className="ml-auto flex items-center gap-1 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs text-zinc-600 hover:border-blue-300 hover:text-blue-600"
          >
            <RefreshCw size={12} />刷新
          </button>
        </div>

        {reportHistory.length === 0 ? (
          <div className="rounded-lg border border-dashed border-zinc-300 p-10 text-center text-sm text-zinc-400">
            暂无历史报告
            <div className="mt-1.5 text-xs text-zinc-300">完成一次「AI 撰写报告」后，报告会出现在这里</div>
          </div>
        ) : (
          <div className="space-y-2">
            {reportHistory.map((h) => (
              <div
                key={h.id}
                className="flex items-center gap-3 rounded-lg border border-zinc-200 bg-white px-4 py-3"
              >
                <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-blue-50">
                  <FileText size={16} className="text-blue-500" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-zinc-800">{h.filename}</span>
                    <span className="text-xs text-zinc-400">{new Date(h.createdAt).toLocaleString()}</span>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-zinc-500">{h.goal}</p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    onClick={() => openReportInNewTab(h)}
                    title={`打开 ${h.filename}（新标签页）`}
                    className="flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs text-zinc-600 hover:border-blue-300 hover:text-blue-600"
                  >
                    <BookOpen size={12} />打开
                  </button>
                  <button
                    onClick={() => handleDeleteReport(h.id)}
                    title="从历史列表移除（保留报告文件）"
                    aria-label={`从历史列表移除 ${h.filename}（保留文件）`}
                    className="flex items-center gap-1 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs text-zinc-500 hover:border-red-300 hover:text-red-500"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );;
}
