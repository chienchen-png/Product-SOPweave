import { build } from "esbuild";
import { spawnSync } from "node:child_process";

await build({
  entryPoints: [
    "tests/fileDrawer.ui.test.tsx",
    "tests/directoryPicker.ui.test.tsx",
  ],
  outdir: ".test-build",
  outExtension: { ".js": ".mjs" },
  bundle: true,
  platform: "node",
  format: "esm",
  packages: "external",
  jsx: "automatic",
});
const result = spawnSync(
  process.execPath,
  [
    "--import",
    "./tests/dom-setup.mjs",
    "--test",
    "--test-concurrency=1",
    ".test-build/fileDrawer.ui.test.mjs",
    ".test-build/directoryPicker.ui.test.mjs",
  ],
  { stdio: "inherit" },
);
process.exitCode = result.status ?? 1;
