import { build } from "esbuild";
import { spawnSync } from "node:child_process";
await build({
  entryPoints: ["tests/skillConfig.test.tsx", "tests/sopSelection.test.tsx", "tests/skillDraftList.test.tsx"],
  outdir: ".test-build/skills",
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
    ".test-build/skills/skillConfig.test.mjs",
    ".test-build/skills/sopSelection.test.mjs",
    ".test-build/skills/skillDraftList.test.mjs",
  ],
  { stdio: "inherit" },
);
process.exitCode = result.status ?? 1;
