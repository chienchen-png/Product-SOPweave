import {build} from 'esbuild';
import {spawnSync} from 'node:child_process';
await build({entryPoints:['tests/workbenchPages.ui.test.tsx','tests/dashboard.ui.test.tsx','tests/dashboardChart.ui.test.tsx'],outdir:'.test-build',outExtension:{'.js':'.mjs'},bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic',loader:{'.css':'empty'},plugins:[{name:'css-stub',setup(builder){
  builder.onResolve({filter:/\.css$/},()=>({path:'css',namespace:'test-css'}));
  builder.onLoad({filter:/.*/,namespace:'test-css'},()=>({contents:'',loader:'js'}));
}},{name:'editor-boundary',setup(builder){
  builder.onResolve({filter:/\/DashboardChart$/},args=>args.importer.includes('Token')?({path:'chart',namespace:'test-chart'}):undefined);
  builder.onResolve({filter:/^echarts\/core$/},()=>({path:'core',namespace:'chart-core'}));
  builder.onLoad({filter:/.*/,namespace:'chart-core'},()=>({contents:`export function use(){}; export function init(){const m=globalThis.__dashboardChartMetrics;m.init++;return {resize(){m.resize++},setOption(){m.update++},dispose(){m.dispose++}}}`,loader:'js'}));
  builder.onLoad({filter:/.*/,namespace:'test-chart'},()=>({contents:'export default function Chart({option,label}){globalThis.__dashboardChartOptions ||= {}; globalThis.__dashboardChartOptions[label]=option;return null}',loader:'js'}));
  builder.onResolve({filter:/\/MarkdownEditor$/},()=>({path:'editor',namespace:'test'}));
  builder.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export default function Editor(){return null}',loader:'js'}));
}}]});
const result=spawnSync(process.execPath,['--import','./tests/dom-setup.mjs','--test','.test-build/workbenchPages.ui.test.mjs','.test-build/dashboard.ui.test.mjs','.test-build/dashboardChart.ui.test.mjs'],{stdio:'inherit'});
process.exitCode=result.status??1;
