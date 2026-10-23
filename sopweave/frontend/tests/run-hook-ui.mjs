import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
await build({entryPoints:['tests/hiddenProject.ui.test.tsx'],outfile:'.test-build/hiddenProject.ui.test.mjs',bundle:true,platform:'node',format:'esm',packages:'external',jsx:'automatic'});
const result=spawnSync(process.execPath,['--import','./tests/dom-setup.mjs','--test','.test-build/hiddenProject.ui.test.mjs'],{stdio:'inherit'});
process.exitCode=result.status??1;
