import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cleanup,render} from '@testing-library/react';
import DashboardChart from '../src/components/dashboard/DashboardChart';

test('dashboard chart observes all resizes updates without reinit and disposes',()=>{
 const metrics={init:0,resize:0,update:0,dispose:0};
 (globalThis as any).__dashboardChartMetrics=metrics;
 const callbacks:ResizeObserverCallback[]=[];let disconnected=0;
 const original=globalThis.ResizeObserver;
 const width=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'clientWidth');
 const height=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'clientHeight');
 Object.defineProperty(HTMLElement.prototype,'clientWidth',{value:600,configurable:true});
 Object.defineProperty(HTMLElement.prototype,'clientHeight',{value:270,configurable:true});
 globalThis.ResizeObserver=class{constructor(callback:ResizeObserverCallback){callbacks.push(callback);}observe(){}unobserve(){}disconnect(){disconnected++;}} as unknown as typeof ResizeObserver;
 try{
  const view=render(<DashboardChart option={{series:[]}} label="测试趋势"/>);
  view.rerender(<DashboardChart option={{series:[{type:'line',data:[1,2]}]}} label="测试趋势"/>);
  callbacks[0]([],{} as ResizeObserver);
  assert.equal(metrics.init,1);assert.equal(metrics.resize,1);assert.ok(metrics.update>=2);
  view.unmount();assert.equal(metrics.dispose,1);assert.equal(disconnected,1);
 }finally{
  cleanup();globalThis.ResizeObserver=original;
  if(width)Object.defineProperty(HTMLElement.prototype,'clientWidth',width);else delete (HTMLElement.prototype as any).clientWidth;
  if(height)Object.defineProperty(HTMLElement.prototype,'clientHeight',height);else delete (HTMLElement.prototype as any).clientHeight;
 }
});

test('dashboard chart releases fifty mount cycles without retained observers',()=>{
 const metrics={init:0,resize:0,update:0,dispose:0};
 (globalThis as any).__dashboardChartMetrics=metrics;
 let disconnected=0;
 const original=globalThis.ResizeObserver;
 const width=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'clientWidth');
 const height=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'clientHeight');
 Object.defineProperty(HTMLElement.prototype,'clientWidth',{value:600,configurable:true});
 Object.defineProperty(HTMLElement.prototype,'clientHeight',{value:270,configurable:true});
 globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){disconnected++;}} as unknown as typeof ResizeObserver;
 try{
  for(let index=0;index<50;index++){
   const view=render(<DashboardChart option={{series:[{type:'line',data:[index,index+1]}]}} label={`循环${index}`}/>);
   view.unmount();
  }
  assert.equal(metrics.init,50);
  assert.equal(metrics.dispose,50);
  assert.equal(disconnected,50);
 }finally{
  cleanup();globalThis.ResizeObserver=original;
  if(width)Object.defineProperty(HTMLElement.prototype,'clientWidth',width);else delete (HTMLElement.prototype as any).clientWidth;
  if(height)Object.defineProperty(HTMLElement.prototype,'clientHeight',height);else delete (HTMLElement.prototype as any).clientHeight;
 }
});
