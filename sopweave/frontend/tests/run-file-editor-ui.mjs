import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
await build({entryPoints:['tests/fileEditor.ui.test.tsx'],outfile:'.test-build/fileEditor.ui.test.mjs',bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic',define:{'import.meta.env.BASE_URL':'"/static/app/"'},plugins:[{name:'editor-port',setup(builder){
  builder.onResolve({filter:/^vditor$/},()=>({path:'vditor',namespace:'test-editor'}));
  builder.onLoad({filter:/.*/,namespace:'test-editor'},()=>({contents:`export default class Vditor { constructor(el, options){globalThis.__vditorOptions=options;globalThis.__vditorReady=false;this.value=options.value;queueMicrotask(()=>{options.after?.();globalThis.__vditorReady=true;});}getValue(){return this.value;}destroy(){} }`,loader:'js'}));
  builder.onResolve({filter:/\.css$/},()=>({path:'css',namespace:'test-css'}));
  builder.onLoad({filter:/.*/,namespace:'test-css'},()=>({contents:'',loader:'js'}));
}}]});
const result=spawnSync(process.execPath,['--import','./tests/dom-setup.mjs','--test','.test-build/fileEditor.ui.test.mjs'],{stdio:'inherit'});
process.exitCode=result.status??1;
