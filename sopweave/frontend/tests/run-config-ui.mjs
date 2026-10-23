import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';

await build({
  entryPoints: ['tests/agentConfig.ui.test.tsx'], outfile: '.test-build/agentConfig.ui.test.mjs',
  bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic',
  plugins: [{ name: 'editor-test-adapter', setup(builder) {
    // Exercise the real configuration save flow without initializing Vditor's
    // browser rendering engine; editor behavior has a separate responsibility.
    builder.onResolve({ filter: /\/MarkdownEditor$/ }, () => ({ path: 'editor', namespace: 'test' }));
    builder.onLoad({ filter: /.*/, namespace: 'test' }, () => ({
      resolveDir: process.cwd(), contents: `import React from 'react';export default function Editor({initialValue,onChange}) { return React.createElement('textarea', {defaultValue:initialValue,onChange:e=>onChange?.(e.target.value)}); }`, loader: 'js',
    }));
  } }],
});
const result = spawnSync(process.execPath, ['--import', './tests/dom-setup.mjs', '--test', '.test-build/agentConfig.ui.test.mjs'], { stdio: 'inherit' });
process.exitCode = result.status ?? 1;
