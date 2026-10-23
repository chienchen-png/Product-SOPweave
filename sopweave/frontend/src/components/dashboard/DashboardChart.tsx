import {useLayoutEffect, useRef} from 'react';
import * as echarts from 'echarts/core';
import type {EChartsOption} from 'echarts';
import {BarChart, LineChart} from 'echarts/charts';
import {GridComponent, TooltipComponent, AriaComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, AriaComponent, CanvasRenderer]);

export default function DashboardChart({option, label, height = 270}: {option: EChartsOption; label: string; height?: number}) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const latest = useRef(option);
  latest.current = option;
  useLayoutEffect(() => {
    const container = element.current;
    if (!container) return;
    const resize = () => {
      if (container.clientWidth < 10 || container.clientHeight < 10) return;
      if (!chart.current) {
        chart.current = echarts.init(container);
        chart.current.setOption(latest.current, true);
      } else chart.current.resize();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);
  useLayoutEffect(() => {chart.current?.setOption(option, true);}, [option]);
  return <div ref={element} role="img" aria-label={label} className="w-full min-w-0" style={{height}}/>;
}
