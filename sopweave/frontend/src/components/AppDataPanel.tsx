/** AppDataPanel — 数据核对应用专属「项目数据库」抽屉（2026-08-21）。
 *
 * 与通用 DataPanel 的区别（应用场景）：
 *  - 数据管理：上传 Excel / 删除数据文件 / 刷新列表
 *  - 索引核对：每文件可「查看索引」（复用 dataIndex 接口 + DataPanel 的 IndexView），
 *    展示表头识别/字段清单/未识别列，供用户确认识别是否正确；
 *    识别有误可删除后重新上传。
 *  - 不支持：引用、写回、修改日志（后续核对/写回流程已被向导固化）
 *  - 上传支持多文件：上传成功后自动触发导入（import_excel_to_db），
 *    进度实时显示；needs_anchor 时提示需要人工确认表头。
 *  - 每次「开始校验」用独立临时库快照，这里删除/重导不影响主库数据平面。
 *
 * 复用现有 API：listDataFiles / uploadFile / importDataFile / importStatus / deleteDataFile / dataIndex
 * 抽屉样式：drawer-anim（右侧滑入），与 FileDrawer/DataPanel 一致。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  X,
  Database,
  RefreshCw,
  Upload,
  Loader2,
  FileSpreadsheet,
  Trash2,
  Eye,
} from "lucide-react";
import {
  listDataFiles,
  uploadFile,
  importDataFile,
  deleteDataFile,
  dataIndex,
  type DataFileRecord,
  type DataSheet,
} from "../lib/api";
import { IndexView } from "./DataPanel";
import ImportTasks from "./ImportTasks";

interface AppDataPanelProps {
  projectId: string;
  closing?: boolean;
  onClose: () => void;
  /** 导入完成回调（通知向导刷新文件下拉） */
  onImported?: () => void;
}

export default function AppDataPanel(props: AppDataPanelProps) {
  return <ApplicationDataPanel key={props.projectId} {...props} />;
}

function ApplicationDataPanel({
  projectId,
  closing,
  onClose,
  onImported,
}: AppDataPanelProps) {
  const [files, setFiles] = useState<DataFileRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const [reloadToken, setReloadToken] = useState(0);
  const uploadInFlight = useRef(false);
  const [uploading, setUploading] = useState(false);
  const [detail, setDetail] = useState<{
    file: DataFileRecord;
    sheets: DataSheet[];
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await listDataFiles(projectId);
      setFiles(data.files || []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Import only files whose upload succeeded; never substitute old bytes. */
  const handleUpload = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList?.length || uploadInFlight.current) return;
      uploadInFlight.current = true;
      setUploading(true);
      setUploadErrors([]);
      const failures: string[] = [];
      try {
        for (const file of Array.from(fileList)) {
          try {
            await uploadFile(projectId, "", file, undefined, {
              reuseIfIdentical: true,
            });
            await importDataFile(projectId, file.name, { alwaysAnchor: true });
            setReloadToken((value) => value + 1);
          } catch (error) {
            failures.push(
              `${file.name}：上传失败或无法启动导入：${(error as Error).message}`,
            );
            setUploadErrors([...failures]);
          }
        }
      } finally {
        uploadInFlight.current = false;
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [projectId],
  );

  /** 删除数据文件（主库数据平面；应用核对用临时库快照，删除不影响已有核对） */
  const handleDelete = useCallback(
    async (file: DataFileRecord) => {
      const ok = window.confirm(
        `确定删除数据文件「${file.file_name}」？\n` +
          `将从项目数据库中移除（${file.sheet_count} 个工作表 · ${(file.total_rows || 0).toLocaleString()} 行）。\n` +
          `删除后向导下拉将不再显示该文件；原始 Excel 仍保留在工作区。`,
      );
      if (!ok) return;
      try {
        await deleteDataFile(file.id);
        if (detail?.file.id === file.id) setDetail(null); // 正在查看该文件索引 → 返回列表
        await load();
        onImported?.();
      } catch (e) {
        alert(`删除失败: ${(e as Error).message}`);
      }
    },
    [load, onImported, detail],
  );

  /** 查看索引：复用 dataIndex 接口展示字段识别结果，供用户核对识别是否正确 */
  const handleIndex = useCallback(async (fileId: string) => {
    try {
      const d = await dataIndex(fileId);
      setDetail({ file: d.file, sheets: d.sheets || [] });
    } catch (e) {
      alert(`查看索引失败: ${(e as Error).message}`);
    }
  }, []);

  return (
    <div
      className={`drawer-anim absolute inset-y-0 right-0 z-20 flex w-[340px] flex-col border-l border-zinc-200 bg-white shadow-2xl ${closing ? "drawer-anim-out" : ""}`}
    >
      {/* 头部：标题 + 关闭 */}
      <div className="flex items-center gap-2 border-b border-zinc-200 px-3.5 py-2.5">
        <Database size={15} className="text-zinc-500" />
        <span className="text-sm font-semibold text-zinc-800">项目数据库</span>
        <span className="rounded-full bg-zinc-100 px-1.5 py-px text-xs text-zinc-500">
          {files.length} 个文件
        </span>
        <button
          onClick={onClose}
          className="ml-auto rounded-md p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600"
          aria-label="关闭数据库"
        >
          <X size={15} />
        </button>
      </div>

      {/* 索引视图：查看某文件的字段识别结果（核对无误后再用于核对） */}
      {detail ? (
        <IndexView
          file={detail.file}
          sheets={detail.sheets || []}
          onBack={() => setDetail(null)}
        />
      ) : (
        <>
          {/* 操作区：上传 + 刷新 */}
          <div className="flex items-center gap-1.5 border-b border-zinc-100 px-3.5 py-2">
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="flex flex-1 items-center justify-center gap-1 rounded-md bg-zinc-900 px-2 py-1.5 text-xs font-medium text-white transition-colors hover:bg-zinc-700 disabled:opacity-50"
            >
              {uploading ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Upload size={12} />
              )}
              {uploading ? "上传中…" : "上传 Excel…"}
            </button>
            <button
              onClick={() => void load()}
              disabled={loading}
              className="flex items-center gap-1 rounded-lg border border-zinc-200 px-2 py-1.5 text-xs text-zinc-600 transition-colors hover:bg-zinc-100 disabled:opacity-50"
              title="刷新列表"
            >
              <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
              刷新
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xls,.xlsx"
              multiple
              className="hidden"
              onChange={(e) => {
                void handleUpload(e.target.files);
              }}
            />
          </div>

          <ImportTasks
            projectId={projectId}
            reloadToken={reloadToken}
            onImported={() => {
              void load();
              onImported?.();
            }}
          />
          {uploadErrors.length > 0 && (
            <div
              role="alert"
              className="max-h-32 overflow-auto border-b px-3 py-2 text-xs text-red-600"
            >
              {uploadErrors.map((message, index) => (
                <p key={index} className="break-words">
                  {message}
                </p>
              ))}
            </div>
          )}

          {/* 文件列表 */}
          <div className="flex-1 overflow-y-auto p-2">
            {loading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-xs text-zinc-400">
                <Loader2 size={13} className="animate-spin" /> 加载中…
              </div>
            ) : error ? (
              <p className="px-2 py-6 text-center text-xs text-red-500">
                {error}
              </p>
            ) : files.length === 0 ? (
              <div className="px-2 py-10 text-center">
                <FileSpreadsheet
                  size={22}
                  className="mx-auto mb-2 text-zinc-300"
                />
                <p className="text-xs text-zinc-400">暂无数据文件</p>
                <p className="mt-0.5 text-xs text-zinc-300">
                  点「上传 Excel…」导入数据表
                </p>
              </div>
            ) : (
              <ul className="space-y-1">
                {files.map((f) => (
                  <li
                    key={f.id}
                    className="group flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-zinc-50"
                  >
                    <FileSpreadsheet
                      size={14}
                      className="shrink-0 text-zinc-400"
                    />
                    <div className="min-w-0 flex-1">
                      <p
                        className="truncate text-xs text-zinc-700"
                        title={f.file_name}
                      >
                        {f.file_name}
                      </p>
                      <p className="text-xs text-zinc-400">
                        {f.sheet_count} 个 sheet ·{" "}
                        {(f.total_rows || 0).toLocaleString()} 行
                        {f.status === "failed" && (
                          <span className="ml-1 text-red-500">导入失败</span>
                        )}
                        {f.needs_reimport && (
                          <span className="ml-1 text-amber-600">
                            建议重新导入
                          </span>
                        )}
                      </p>
                    </div>
                    <button
                      onClick={() => void handleIndex(f.id)}
                      className="shrink-0 rounded-md p-1 text-zinc-300 transition-colors hover:bg-zinc-100 hover:text-zinc-600"
                      title="查看索引（核对字段识别是否正确）"
                    >
                      <Eye size={13} />
                    </button>
                    <button
                      onClick={() => void handleDelete(f)}
                      className="shrink-0 rounded-md p-1 text-zinc-300 transition-colors hover:bg-red-50 hover:text-red-500"
                      title="删除该文件"
                    >
                      <Trash2 size={13} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {/* 底部说明 */}
      <div className="border-t border-zinc-100 px-3.5 py-2">
        <p className="text-xs leading-relaxed text-zinc-400">
          数据库用于向导选择核对文件。每次「开始校验」会在独立临时库执行，
          此处的删除/刷新不影响已完成的核对结果。上传 Excel 后自动导入入库，
          可点 <Eye size={9} className="inline text-zinc-400" />{" "}
          查看索引核对字段识别，识别有误可删除后重新上传。
        </p>
      </div>
    </div>
  );
}
