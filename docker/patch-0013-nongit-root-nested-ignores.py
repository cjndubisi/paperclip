#!/usr/bin/env python3
"""PATCH-0013: a non-git workspace root must still honour its nested repos' .gitignore.

Evidence (run b921c696, Argus/REM-245, 2026-09-26): the project had no workspace
row, so Paperclip used the fallback `_default/` dir with the real checkout nested at
`_default/remoteafrica/`. `readLocalGitWorkspaceSnapshot(_default)` is null, so
`prepareWorkspaceForSshExecution` took the non-git branch whose ONLY exclude is
`.paperclip-runtime`. PATCH-0008/0009 (node_modules floor + gitignore set) live on the
git branch and never ran. Result: 972 MB uploaded, 2.9 GB+ copied back (three
node_modules trees 1.2 G / 1.4 G / 172 M plus a 241 MB .next), ~20 min of the 71 min run
spent restoring dependency trees the agent had installed.

Fix: in the non-git branch, exclude
  * a floor of dependency/build dirs that are never source (`node_modules`, `.next`) at
    any depth, and
  * every depth-1 nested git repo's own `git status --ignored` set, prefixed with the
    repo's directory, which is exactly what the git branch already does for a root repo.
The same list is returned as `ignoredExcludes`, so remote-managed-runtime applies it to
the baseline snapshot and therefore to the restore (both walks honour it).
Nested `.git` dirs still sync: the agent needs git inside the checkout.
"""
from pathlib import Path
import sys

MARKER = "PATCH-0013-nongit-root-nested-ignores"

ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
rmr = Path('/app/packages/adapter-utils/src/remote-managed-runtime.ts')
for p in (ssh, rmr):
    if not p.exists():
        raise SystemExit(f'{p} not found')

# ---------------------------------------------------------------- ssh.ts
s = ssh.read_text()
if MARKER not in s:
    if 'PATCH-0009-gitignore-aware-archive' not in s:
        raise SystemExit('ssh.ts: PATCH-0013 requires PATCH-0009 (ignoredExcludes contract) first')

    OLD_NONGIT = '''  await syncDirectoryToSsh({
    spec: input.spec,
    localDir: input.localDir,
    remoteDir,
    exclude: [".paperclip-runtime"],
    onProgress: input.onProgress,
    progressLabel: "workspace",
  });
  return { gitBacked: false, ignoredExcludes: [] };
}'''
    NEW_NONGIT = '''  // PATCH-0013-nongit-root-nested-ignores: a fallback root such as `_default/`
  // holding the checkout one level down is not itself a repo, so the git branch
  // above (and its node_modules floor + gitignore set) never ran for it.
  const nestedExcludes = await sshNonGitRootExcludes(input.localDir);
  await syncDirectoryToSsh({
    spec: input.spec,
    localDir: input.localDir,
    remoteDir,
    exclude: [".paperclip-runtime", ...nestedExcludes],
    onProgress: input.onProgress,
    progressLabel: "workspace",
  });
  return { gitBacked: false, ignoredExcludes: nestedExcludes };
}'''
    if s.count(OLD_NONGIT) != 1:
        raise SystemExit(f'ssh.ts: PATCH-0013 non-git anchor count {s.count(OLD_NONGIT)} != 1')
    s = s.replace(OLD_NONGIT, NEW_NONGIT, 1)

    ANCHOR = 'export async function prepareWorkspaceForSshExecution(input: {'
    HELPER = '''// PATCH-0013-nongit-root-nested-ignores: dependency/build output that is never
// source, excluded at any depth (the four forms satisfy both tar's matcher and
// the snapshot walker's shouldExcludePath).
const SSH_NONGIT_HEAVY_DIR_NAMES = ["node_modules", ".next"] as const;

export function sshNonGitHeavyDirExcludes(): string[] {
  return SSH_NONGIT_HEAVY_DIR_NAMES.flatMap((entry) => [entry, `${entry}/*`, `*/${entry}`, `*/${entry}/*`]);
}

// For a non-git workspace root, apply each depth-1 nested repo's own ignore set,
// prefixed with the repo's directory. Failure of any single repo scan is
// non-fatal: that repo just falls back to the heavy-dir floor.
export async function sshNonGitRootExcludes(localDir: string): Promise<string[]> {
  const out = [...sshNonGitHeavyDirExcludes()];
  let entries: import("node:fs").Dirent[] = [];
  try {
    entries = await fs.readdir(localDir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name === ".paperclip-runtime" || entry.name.startsWith("..")) continue;
    const repoDir = path.join(localDir, entry.name);
    try {
      await fs.stat(path.join(repoDir, ".git"));
    } catch {
      continue;
    }
    try {
      const result = await runLocalGit(
        repoDir,
        ["status", "--ignored", "--porcelain=v1", "-z", "--untracked-files=normal"],
        { timeout: 20_000, maxBuffer: 1024 * 1024 },
      );
      const ignored = result.stdout
        .split("\\0")
        .filter((line) => line.startsWith("!! "))
        .map((line) => `${entry.name}/${line.slice(3).replace(/\\/+$/, "")}`);
      out.push(...sshGitWorkspaceIgnoredExcludes(ignored));
    } catch {
      // keep the floor for this repo
    }
  }
  return [...new Set(out)];
}

'''
    if s.count(ANCHOR) != 1:
        raise SystemExit('ssh.ts: PATCH-0013 helper anchor not found exactly once')
    s = s.replace(ANCHOR, HELPER + ANCHOR, 1)
    ssh.write_text(s)
    print('patched:' + str(ssh))
else:
    print('ssh.ts already-current')

# ------------------------------------------------- remote-managed-runtime.ts
r = rmr.read_text()
if MARKER not in r:
    OLD = '''      ? [...remoteManagedGitWorkspaceExcludes(), ...preparedWorkspace.ignoredExcludes]
      : [".paperclip-runtime"]'''
    NEW = '''      ? [...remoteManagedGitWorkspaceExcludes(), ...preparedWorkspace.ignoredExcludes]
      // PATCH-0013-nongit-root-nested-ignores: same list the upload used, so the
      // restore never walks or copies back nested dependency/build trees.
      : [".paperclip-runtime", ...preparedWorkspace.ignoredExcludes]'''
    if r.count(OLD) != 1:
        raise SystemExit('remote-managed-runtime.ts: PATCH-0013 baseline anchor not found (needs PATCH-0009)')
    r = r.replace(OLD, NEW, 1)
    rmr.write_text(r)
    print('patched:' + str(rmr))
else:
    print('remote-managed-runtime.ts already-current')
