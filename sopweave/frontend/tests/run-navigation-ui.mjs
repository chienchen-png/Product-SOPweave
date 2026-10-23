import { build } from 'esbuild';
import { spawnSync } from 'node:child_process';

await build({
  entryPoints: ['tests/appNavigation.ui.test.tsx'], outfile: '.test-build/appNavigation.ui.test.mjs',
  bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic',
  plugins: [{ name: 'navigation-boundaries', setup(builder) {
    builder.onResolve({ filter: /\/(pages\/|layout\/NavSidebar|context\/AiRegistryContext|components\/NewProjectDialog)/ }, args => ({ path: args.path, namespace: 'test' }));
    builder.onLoad({ filter: /.*/, namespace: 'test' }, args => {
      const name = args.path.split('/').pop();
      const contents = name === 'NavSidebar'
        ? `import React from 'react';export default function Nav({onNavigate}) { return React.createElement('nav',null,...['app-sales-check','app-finmod'].map(v=>React.createElement('button',{key:v,onClick:()=>onNavigate(v)},v))); }`
        : name === 'AiRegistryContext'
        ? `export const useAiRegistry=()=>({models:[],agents:[],providers:[],loading:globalThis.__registryLoading??false});`
        : `import React from 'react';(globalThis.__loadedPages??=[]).push('${name}');export default function Page({project}){ return React.createElement('div',null,'${name} mounted'+(project?' '+project.id:'')); }`;
      return { contents, loader: 'js', resolveDir: process.cwd() };
    });
  } }],
});
const result = spawnSync(process.execPath, ['--import', './tests/dom-setup.mjs', '--test', '.test-build/appNavigation.ui.test.mjs'], { stdio: 'inherit' });
process.exitCode = result.status ?? 1;
