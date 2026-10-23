import {build} from 'esbuild';
import {spawnSync} from 'node:child_process';
await build({entryPoints:['tests/chatComposer.ui.test.tsx'],outfile:'.test-build/chatComposer.ui.test.mjs',bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic',plugins:[{name:'dialog-boundaries',setup(builder){
  builder.onResolve({filter:/\.css$/},()=>({path:'empty',namespace:'empty'}));
  builder.onLoad({filter:/.*/,namespace:'empty'},()=>({contents:'',loader:'js'}));
  builder.onResolve({filter:/\/(FileDrawer|DataPanel|SkillCreateWizard|SaveConversationDialog)$/},args=>args.importer.endsWith('ChatPage.tsx')?{path:'unused-dialog',namespace:'dialog'}:undefined);
  builder.onLoad({filter:/.*/,namespace:'dialog'},()=>({contents:'export default function Dialog(){return null}',loader:'js'}));
}}]});
const result=spawnSync(process.execPath,['--import','./tests/dom-setup.mjs','--test','.test-build/chatComposer.ui.test.mjs'],{stdio:'inherit'});
process.exitCode=result.status??1;
