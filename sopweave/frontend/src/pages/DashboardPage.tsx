import {useCallback, useEffect, useRef, useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {fetchDashboardSummary, formatCount, formatDuration, type DashboardDays, type DashboardSummary} from '../lib/dashboard';
import PageHeader from '../components/ui/PageHeader';
import StatCard from '../components/dashboard/StatCard';
import TokenTrendChart from '../components/dashboard/TokenTrendChart';
import TokenRankingChart from '../components/dashboard/TokenRankingChart';

function localClock(value: Date) {
  return [value.getHours(), value.getMinutes(), value.getSeconds()]
    .map(part => String(part).padStart(2, '0'))
    .join(':');
}

export default function DashboardPage() {
  const [days, setDays] = useState<DashboardDays>(7);
  const [dimension, setDimension] = useState<'providers' | 'models'>('providers');
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const version = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    const id = ++version.current;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setLoading(true);
    try {
      const value = await fetchDashboardSummary(days, request.signal);
      if (id === version.current) {setData(value); setError('');}
    } catch (cause) {
      if (id === version.current && !request.signal.aborted) setError((cause as Error).message);
    } finally {
      if (id === version.current) setLoading(false);
    }
  }, [days]);
  useEffect(() => {
    void load();
    let timer: number | undefined;
    const pause = () => {window.clearInterval(timer); timer = undefined;};
    const start = () => {
      pause();
      if (document.visibilityState !== 'hidden') timer = window.setInterval(() => {
        if (document.visibilityState !== 'hidden') void load();
      }, 30000);
    };
    const visibility = () => {start(); if (document.visibilityState !== 'hidden') void load();};
    start();
    document.addEventListener('visibilitychange', visibility);
    return () => {pause(); version.current++; controller.current?.abort(); document.removeEventListener('visibilitychange', visibility);};
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const today = data?.today;
  const quality = data?.quality;
  const missing = quality?.today_missing;
  const rangeMissing = quality?.range_missing;
  const rank = data?.token_ranking[dimension] || [];
  return <main className="app-page flex min-h-0 min-w-0 flex-1 flex-col">
    <PageHeader title="工作台" description="查看运行概况与模型用量。" actions={<>
      <time dateTime={now.toISOString()} title={now.toLocaleString('zh-CN')} className="flex items-center gap-1.5 text-xs text-zinc-500">
        <span>本机时间</span>
        <span className="font-medium tabular-nums text-zinc-700">{localClock(now)}</span>
      </time>
      <button className="ui-icon-button" aria-label="刷新统计" title="刷新统计" disabled={loading} onClick={() => void load()}><RefreshCw size={16} className={loading ? 'animate-spin' : ''}/></button>
    </>}/>
    <div className="page-body min-h-0 flex-1 overflow-y-auto"><div className="page-content space-y-5">
      {error && <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">统计刷新失败：{error}。{data ? '当前保留上次成功数据。' : '请重试。'}<button className="ml-3 underline" onClick={() => void load()}>重试</button></div>}
      <section aria-label="今日统计" aria-busy={loading} className="ui-panel dashboard-stats grid grid-cols-2 lg:grid-cols-4">
        <StatCard label="今日 Token" value={formatCount(today?.tokens)} sub={!quality?.tokens_available ? (loading ? '正在加载用量' : '用量统计不可用') : missing ? `今日 ${missing} 次请求未返回用量` : '供应商返回的确认用量'} hint="输入与输出 Token 总量。推理 Token 已包含在供应商总量中；缺失用量不估算。"/>
        <StatCard label="今日 AI 请求" value={formatCount(today?.ai_requests)} sub="包含失败和取消的模型调用" hint="一次模型调用计一次。多轮工具执行与应用重试分别计数；SDK 内部网络自动重试不拆分。"/>
        <StatCard label="今日运行" value={formatCount(today?.runs)} sub={quality?.runtime_available ? `成功 ${formatCount(today?.success)} · 失败 ${formatCount(today?.failed)}` : '运行统计不可用'}/>
        <StatCard label="应用运行时长" value={formatDuration(today?.duration_sec)} sub="今日服务启动至关闭的累计时长" hint="按本地日期计算，重叠服务会话仅计一次。"/>
      </section>
      <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
        <p className="text-xs text-zinc-500">{data ? `上次更新 ${new Date(data.ts).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', second: '2-digit'})} · ${data.timezone}` : loading ? '正在加载统计…' : '统计尚未加载'}</p>
        <div className="ui-tabs" role="group" aria-label="统计范围">{([7, 30] as const).map(value => <button key={value} className="ui-tab" aria-pressed={days === value} onClick={() => setDays(value)}>{value} 日</button>)}</div>
      </div>
      <div className="dashboard-charts">
        <section aria-labelledby="token-trend-title" className="ui-panel min-w-0 p-5">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h2 id="token-trend-title" className="ui-section-title">Token 消耗趋势</h2><span className="text-xs text-zinc-500">{data ? `${data.range.start} — ${data.range.end}` : '每日总量'}</span></div>
          {quality?.tokens_available && data?.token_trend.some(point => point.tokens != null) ? <TokenTrendChart points={data.token_trend}/> : <div className="dashboard-chart-empty">{loading && !data ? '加载用量…' : quality?.tokens_available ? '暂无已确认用量' : '用量统计暂不可用'}</div>}
          <p className="mt-2 text-xs text-zinc-500">{rangeMissing ? `区间内 ${rangeMissing} 次请求缺失用量，曲线仅含已确认部分。` : '按调用开始日期汇总；空白日期表示尚未采集或用量缺失。'}</p>
        </section>
        <section aria-labelledby="token-ranking-title" className="ui-panel min-w-0 p-5">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><h2 id="token-ranking-title" className="ui-section-title">模型用量</h2><div className="ui-tabs dashboard-dimension" role="group" aria-label="排行维度">{([['providers', '供应商'], ['models', '模型']] as const).map(([key, label]) => <button key={key} className="ui-tab" aria-pressed={dimension === key} onClick={() => setDimension(key)}>{label}</button>)}</div></div>
          {quality?.tokens_available && rank.length ? <TokenRankingChart items={rank}/> : <div className="dashboard-chart-empty">{loading && !data ? '加载排行…' : quality?.tokens_available ? '暂无已确认用量' : '用量统计暂不可用'}</div>}
          <p className="mt-2 text-xs text-zinc-500">同一统计范围 · 最多展示前 8 项，其余合并</p>
        </section>
      </div>
      <footer className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
        <span>{quality?.tracking_started_at ? `用量自 ${new Date(quality.tracking_started_at).toLocaleDateString('zh-CN')} 开始采集，历史未采集数据不补算。` : '启用采集后开始记录真实用量。'}</span>
        {quality?.collector_warning && <span role="status" className="text-amber-700">采集曾发生异常，部分用量可能缺失。</span>}
        {data && !quality?.runtime_available && <span role="status" className="text-amber-700">运行统计暂不可用。</span>}
      </footer>
    </div></div>
  </main>;
}
