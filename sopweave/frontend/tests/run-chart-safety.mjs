import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
await build({ entryPoints: ['tests/chartSafety.test.ts'], outfile: '.test-build/chartSafety.test.mjs', bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic', plugins: [{name:'omit-css',setup(builder){builder.onResolve({filter:/\.css$/},()=>({path:'css',namespace:'test-css'}));builder.onLoad({filter:/.*/,namespace:'test-css'},()=>({contents:'',loader:'js'}));}}] });
const result = spawnSync(process.execPath, ['--test', '.test-build/chartSafety.test.mjs'], { stdio: 'inherit' });
process.exitCode = result.status ?? 1;
