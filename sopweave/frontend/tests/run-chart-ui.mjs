import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
await build({entryPoints:['tests/chartPreview.ui.test.tsx'],outfile:'.test-build/chartPreview.ui.test.mjs',bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic',plugins:[{name:'canvas-boundary',setup(builder){
  builder.onResolve({filter:/^echarts(?:\/core|-gl)?$/},()=>({path:'echarts',namespace:'test-chart'}));
  builder.onLoad({filter:/.*/,namespace:'test-chart'},()=>({contents:'export const graphic={LinearGradient:class{}};export function init(){return {setOption(){},resize(){},dispose(){}}}',loader:'js'}));
}}]});
const result=spawnSync(process.execPath,['--import','./tests/dom-setup.mjs','--test','.test-build/chartPreview.ui.test.mjs'],{stdio:'inherit'});
process.exitCode=result.status??1;
