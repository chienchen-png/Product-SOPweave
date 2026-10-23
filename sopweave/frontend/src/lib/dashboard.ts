import {request} from './api/client';

export type DashboardDays = 7 | 30;
export interface TokenPoint {day: string; tokens: number | null; missing_requests: number;}
export interface ProviderUsage {id: string; name: string; tokens: number; other?: boolean;}
export interface ModelUsage {provider_id: string; model_id: string; provider_name: string; name: string; tokens: number; other?: boolean;}
export interface DashboardSummary {
  ts: string;
  timezone: string;
  range: {days: DashboardDays; start: string; end: string};
  today: {tokens: number | null; ai_requests: number | null; runs: number | null; success: number | null; failed: number | null; duration_sec: number | null};
  token_trend: TokenPoint[];
  token_ranking: {providers: ProviderUsage[]; models: ModelUsage[]};
  quality: {tracking_started_at: string | null; today_missing: number | null; range_missing: number | null; tokens_available: boolean; runtime_available: boolean; collector_warning: boolean};
}

export function fetchDashboardSummary(days: DashboardDays, signal?: AbortSignal): Promise<DashboardSummary> {
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai';
  return request(`/api/dashboard/summary?${new URLSearchParams({days: String(days), timezone})}`, {signal});
}

export function formatCount(value: number | null | undefined): string {
  return value == null ? '—' : new Intl.NumberFormat('zh-CN').format(value);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分`;
  if (seconds >= 60) return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
  return `${Math.floor(seconds)} 秒`;
}
