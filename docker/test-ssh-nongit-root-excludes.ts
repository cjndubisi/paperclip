import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { sshNonGitRootExcludes, sshNonGitHeavyDirExcludes } from "./ssh.js";
import { shouldExcludePath } from "./exclude-patterns.js";

// PATCH-0013-nongit-root-nested-ignores
describe("non-git workspace root with a nested checkout", () => {
  const root = mkdtempSync(path.join(os.tmpdir(), "pc13-"));
  const repo = path.join(root, "remoteafrica");
  mkdirSync(path.join(repo, "node_modules/x"), { recursive: true });
  mkdirSync(path.join(repo, "cloud_functions/functions/node_modules/y"), { recursive: true });
  mkdirSync(path.join(repo, "cloud_functions/functions/lib"), { recursive: true });
  mkdirSync(path.join(repo, ".next/cache"), { recursive: true });
  mkdirSync(path.join(repo, "scripts/e2e"), { recursive: true });
  writeFileSync(path.join(repo, ".gitignore"), "node_modules\n.next\nlib/\n.typesense-data/\n");
  writeFileSync(path.join(repo, "scripts/e2e/up.sh"), "echo up\n");
  writeFileSync(path.join(repo, "node_modules/x/i.js"), "1");
  writeFileSync(path.join(repo, "cloud_functions/functions/lib/index.js"), "1");
  mkdirSync(path.join(repo, ".typesense-data"), { recursive: true });
  writeFileSync(path.join(repo, ".typesense-data/db"), "1");
  execFileSync("git", ["init", "-q", repo]);
  execFileSync("git", ["-C", repo, "add", ".gitignore", "scripts"]);

  it("excludes dependency/build trees and gitignored paths, keeps source and .git", async () => {
    const ex = await sshNonGitRootExcludes(root);
    const hit = (p: string) => shouldExcludePath(p, ex);
    expect(hit("remoteafrica/node_modules")).toBe(true);
    expect(hit("remoteafrica/node_modules/x/i.js")).toBe(true);
    expect(hit("remoteafrica/cloud_functions/functions/node_modules/y")).toBe(true);
    expect(hit("remoteafrica/.next/cache")).toBe(true);
    expect(hit("remoteafrica/cloud_functions/functions/lib/index.js")).toBe(true);
    expect(hit("remoteafrica/.typesense-data/db")).toBe(true);
    expect(hit("remoteafrica/scripts/e2e/up.sh")).toBe(false);
    expect(hit("remoteafrica/.git/HEAD")).toBe(false);
    expect(hit("remoteafrica/.gitignore")).toBe(false);
  });

  it("falls back to the heavy-dir floor when the root cannot be read", async () => {
    expect(await sshNonGitRootExcludes(path.join(root, "missing"))).toEqual(sshNonGitHeavyDirExcludes());
  });
});
