import {lazy, Suspense, useMemo} from 'react';
import type {EChartsOption} from 'echarts';
import type {ModelUsage, ProviderUsage} from '../../lib/dashboard';
const DashboardChart = lazy(() => import('./DashboardChart'));

export default function TokenRankingChart({items}: {items: (ProviderUsage | ModelUsage)[]}) {
  const option = useMemo<EChartsOption>(() => ({
    animation: false,
    textStyle: {fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif', fontSize: 12, color: '#64666f'},
    grid: {left: 8, right: 54, top: 12, bottom: 12, containLabel: true},
    tooltip: {trigger: 'axis', renderMode: 'richText', axisPointer: {type: 'shadow'}},
    xAxis: {type: 'value', show: false},
    yAxis: {type: 'category', inverse: true, data: items.map(item => 'provider_name' in item && item.provider_name ? `${item.name} · ${item.provider_name}` : item.name),
      axisLine: {show: false}, axisTick: {show: false}, axisLabel: {width: 120, overflow: 'truncate', margin: 12}},
    series: [{name: 'Token', type: 'bar', barMaxWidth: 14, data: items.map(item => item.tokens),
      itemStyle: {color: '#7c9ddc', borderRadius: [0, 3, 3, 0]},
      label: {show: true, position: 'right', color: '#64666f', formatter: params => Number(params.value).toLocaleString('zh-CN')}}],
  }), [items]);
  return <Suspense fallback={<div className="dashboard-chart-empty">加载图表…</div>}><DashboardChart option={option} label={items.map(item => `${item.name}: ${item.tokens} Token`).join('；')}/></Suspense>;
}
