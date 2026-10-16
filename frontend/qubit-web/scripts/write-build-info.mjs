// Writes frontend/qubit-dist/build-info.json after `vite build` so the
// dashboard backend (and operators) can tell a stale bundle from a current
// one without guessing. Best-effort: a missing git binary or dirty tree is
// recorded, never fatal to the build.
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");
const outPath = resolve(here, "..", "..", "qubit-dist", "build-info.json");

function git(args) {
  try {
    return execFileSync("git", args, { cwd: repoRoot, encoding: "utf8" }).trim();
  } catch {
    return "";
  }
}

const head = git(["rev-parse", "HEAD"]);
const dirty = git(["status", "--porcelain"]);
const info = {
  schema: "qubit-dashboard-build/1",
  source_head: head || "unknown",
  source_dirty: dirty ? true : head === "",
  built_at: new Date().toISOString(),
};
writeFileSync(outPath, JSON.stringify(info, null, 2) + "\n");
console.log(`build-info: ${outPath} head=${info.source_head.slice(0, 12)} dirty=${info.source_dirty}`);
