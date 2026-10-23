import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';
await build({ entryPoints: ['tests/agentInteraction.ui.test.tsx'], outfile: '.test-build/agentInteraction.ui.test.mjs',
  bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic' });
const result = spawnSync(process.execPath, ['--import', './tests/dom-setup.mjs', '--test', '.test-build/agentInteraction.ui.test.mjs'], { stdio: 'inherit' });
process.exitCode = result.status ?? 1;
