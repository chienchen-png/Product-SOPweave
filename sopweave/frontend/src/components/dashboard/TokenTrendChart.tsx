import {lazy, Suspense, useMemo} from 'react';
import type {EChartsOption} from 'echarts';
import type {TokenPoint} from '../../lib/dashboard';
const DashboardChart = lazy(() => import('./DashboardChart'));

export default function TokenTrendChart({points}: {points: TokenPoint[]}) {
  const option = useMemo<EChartsOption>(() => ({
    animation: false,
    textStyle: {fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif', fontSize: 12, color: '#64666f'},
    grid: {left: 16, right: 20, top: 24, bottom: 12, containLabel: true},
    tooltip: {trigger: 'axis', renderMode: 'richText', valueFormatter: value => value == null ? '用量未返回' : Number(value).toLocaleString('zh-CN')},
    xAxis: {type: 'category', boundaryGap: false, data: points.map(point => point.day),
      axisLine: {lineStyle: {color: '#e5e7eb'}}, axisTick: {show: false},
      axisLabel: {margin: 14, formatter: (day: string) => day.slice(5).replace('-', '/')}},
    yAxis: {type: 'value', minInterval: 1, axisLabel: {formatter: (value: number) => value >= 1000 ? `${value / 1000}k` : String(value)}, splitLine: {lineStyle: {color: '#eef0f3', type: 'dashed'}}},
    series: [{name: 'Token', type: 'line', data: points.map(point => point.tokens),
      connectNulls: false, showSymbol: true, showAllSymbol: true, symbolSize: 5,
      lineStyle: {width: 2, color: '#4f7ddb'}, itemStyle: {color: '#4f7ddb'},
      areaStyle: {color: '#4f7ddb', opacity: 0.05}}],
  }), [points]);
  return <Suspense fallback={<div className="dashboard-chart-empty">加载图表…</div>}><DashboardChart option={option} label="每日 Token 总量，空白日期表示未采集或用量缺失"/></Suspense>;
}
