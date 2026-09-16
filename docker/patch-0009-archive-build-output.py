#!/usr/bin/env python3
"""PATCH-0009 (v2): honour .gitignore in the SSH workspace archive.

WHY the previous hardcoded-list version was the wrong shape
-----------------------------------------------------------
The SANDBOX driver already resolves the repo's own ignore set and feeds it to
tar: `sandbox-managed-runtime.ts` computes `gitSnapshot.ignoredPaths` (from
`git status --ignored`) and merges it into BOTH `workspaceArchiveExclude` and
`restoreExclude`.

The SSH driver computes the exact same `ignoredPaths` -- `readLocalGitWorkspaceSnapshot`
in `git-workspace-sync.ts` returns it -- and then never uses it. Verified:
`grep -c ignoredPaths ssh.ts remote-managed-runtime.ts` == 0 in both files.
`sshGitWorkspaceArchiveExcludes()` is a fixed 4-entry list, so every gitignored
build artifact (.next, lib/, dist/, coverage, playwright-report, test-results)
ships to the sprite and back on every run.

So the defect is NOT "the exclude list is too short" -- it is "the SSH path drops
the ignore set the sandbox path honours". Hardcoding a longer list papers over
that and silently diverges from each repo's actual .gitignore. This patch instead
threads the already-computed ignoredPaths into both directions, so the SSH driver
matches the sandbox driver and automatically respects whatever the repo ignores.

`sshGitWorkspaceArchiveExcludes()` stays as the fixed floor (.git, runtime dir,
sibling worktrees, node_modules) because node_modules is NOT always gitignored in
a repo that vendors it, and .paperclip/worktrees is Paperclip's own, not the repo's.
"""
from pathlib import Path
import sys

MARKER = "PATCH-0009-gitignore-aware-archive"

ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
rmr = Path('/app/packages/adapter-utils/src/remote-managed-runtime.ts')
for p in (ssh, rmr):
    if not p.exists():
        raise SystemExit(f'{p} not found')

s = ssh.read_text()

# ---------------------------------------------------------------- ssh.ts
# 1. Revert the v1 hardcoded list back to the upstream 4-entry floor.
V1_LIST = '''export function sshGitWorkspaceArchiveExcludes(): string[] {
  // PATCH-0009-archive-build-output: framework build output, caches and test
  // artifacts are gitignored and regenerable on the execution target. Carrying
  // them made the sync-back archive multi-GB, and a multi-minute tar stream
  // over the sprite ssh proxy corrupts mid-flight ("does not look like a tar
  // archive"). Same set upstream already prunes for referenced projects.
  return [
    ".git",
    ".paperclip-runtime",
    ".paperclip/worktrees",
    "node_modules",
    ".next",
    ".turbo",
    ".cache",
    ".yarn/cache",
    ".venv",
    "dist",
    "build",
    "out",
    "coverage",
    "playwright-report",
    "test-results",
    "blob-report",
    ".playwright",
  ];
}'''
FLOOR = '''export function sshGitWorkspaceArchiveExcludes(): string[] {
  return [".git", ".paperclip-runtime", ".paperclip/worktrees", "node_modules"];
}'''
if V1_LIST in s:
    s = s.replace(V1_LIST, FLOOR, 1)

if MARKER not in s:
    if FLOOR not in s:
        raise SystemExit('ssh.ts: PATCH-0009 floor helper anchor not found')

    # 2. Return the git snapshot's ignoredPaths from prepare, so the caller can
    #    reuse the SAME set for the baseline + restore (no second git walk).
    OLD_SIG = '''export async function prepareWorkspaceForSshExecution(input: {
  spec: SshRemoteExecutionSpec;
  localDir: string;
  remoteDir?: string;
  onProgress?: RuntimeProgressSink;
}): Promise<{ gitBacked: boolean }> {'''
    NEW_SIG = '''// PATCH-0009-gitignore-aware-archive: the sandbox driver merges the repo's own
// `git status --ignored` set into its tar excludes; the SSH driver computed the
// same set and discarded it, so every gitignored build artifact crossed the wire
// twice per run. Return it so the caller applies it to the archive, the baseline
// snapshot and the restore -- matching sandbox behaviour and each repo's actual
// .gitignore instead of a hardcoded guess.
export function sshGitWorkspaceIgnoredExcludes(ignoredPaths: string[]): string[] {
  const out: string[] = [];
  for (const raw of ignoredPaths) {
    const entry = raw.replace(/^\\.\\//, "").replace(/\\/+$/, "");
    if (!entry || entry.startsWith("..") || entry.startsWith("/")) continue;
    // Escape tar glob metacharacters so a literal path is matched literally.
    const literal = entry.replace(/\\\\/g, "\\\\\\\\").replace(/([*?[])/g, "\\\\$1");
    out.push(literal, `${literal}/*`);
  }
  return [...new Set(out)];
}

export async function prepareWorkspaceForSshExecution(input: {
  spec: SshRemoteExecutionSpec;
  localDir: string;
  remoteDir?: string;
  onProgress?: RuntimeProgressSink;
}): Promise<{ gitBacked: boolean; ignoredExcludes: string[] }> {'''
    if OLD_SIG not in s:
        raise SystemExit('ssh.ts: PATCH-0009 prepare signature anchor not found')
    s = s.replace(OLD_SIG, NEW_SIG, 1)

    # 3. Apply the ignore set to the upload archive and report it back.
    OLD_UP = '''    await syncDirectoryToSsh({
      spec: input.spec,
      localDir: input.localDir,
      remoteDir,
      exclude: sshGitWorkspaceArchiveExcludes(),
      onProgress: input.onProgress,
      progressLabel: "workspace",
    });
    await removeDeletedPathsOnSsh({
      spec: input.spec,
      remoteDir,
      deletedPaths: gitSnapshot.deletedPaths,
    });
    return { gitBacked: true };'''
    NEW_UP = '''    const ignoredExcludes = sshGitWorkspaceIgnoredExcludes(gitSnapshot.ignoredPaths ?? []);
    await syncDirectoryToSsh({
      spec: input.spec,
      localDir: input.localDir,
      remoteDir,
      exclude: [...sshGitWorkspaceArchiveExcludes(), ...ignoredExcludes],
      onProgress: input.onProgress,
      progressLabel: "workspace",
    });
    await removeDeletedPathsOnSsh({
      spec: input.spec,
      remoteDir,
      deletedPaths: gitSnapshot.deletedPaths,
    });
    return { gitBacked: true, ignoredExcludes };'''
    if OLD_UP not in s:
        raise SystemExit('ssh.ts: PATCH-0009 upload anchor not found')
    s = s.replace(OLD_UP, NEW_UP, 1)

    OLD_NONGIT = '''    progressLabel: "workspace",
  });
  return { gitBacked: false };
}'''
    NEW_NONGIT = '''    progressLabel: "workspace",
  });
  return { gitBacked: false, ignoredExcludes: [] };
}'''
    if OLD_NONGIT not in s:
        raise SystemExit('ssh.ts: PATCH-0009 non-git return anchor not found')
    s = s.replace(OLD_NONGIT, NEW_NONGIT, 1)

    ssh.write_text(s)
    print('patched:' + str(ssh))
else:
    print('ssh.ts already-current')

# ------------------------------------------------- remote-managed-runtime.ts
r = rmr.read_text()
if MARKER not in r:
    OLD_BASE = '''  const baselineSnapshot = preparedWorkspace
    ? await captureDirectorySnapshot(input.workspaceLocalDir, {
        exclude: preparedWorkspace.gitBacked
          ? remoteManagedGitWorkspaceExcludes()
          : [".paperclip-runtime"],
      })
    : null;'''
    NEW_BASE = '''  // PATCH-0009-gitignore-aware-archive: the baseline and the restore must use the
  // SAME exclude set as the upload, or the restore walks (and hashes) gigabytes of
  // gitignored build output the upload never sent.
  const workspaceExcludes = preparedWorkspace
    ? preparedWorkspace.gitBacked
      ? [...remoteManagedGitWorkspaceExcludes(), ...preparedWorkspace.ignoredExcludes]
      : [".paperclip-runtime"]
    : [];
  const baselineSnapshot = preparedWorkspace
    ? await captureDirectorySnapshot(input.workspaceLocalDir, {
        exclude: workspaceExcludes,
      })
    : null;'''
    if OLD_BASE not in r:
        raise SystemExit('remote-managed-runtime.ts: PATCH-0009 baseline anchor not found')
    r = r.replace(OLD_BASE, NEW_BASE, 1)
    rmr.write_text(r)
    print('patched:' + str(rmr))
else:
    print('remote-managed-runtime.ts already-current')

# The v1 test asserted a long hardcoded array; restore the upstream floor assertion.
test = Path('/app/packages/adapter-utils/src/ssh-workspace-exclusions.test.ts')
if test.exists():
    t = test.read_text()
    want = '''import { describe, expect, it } from "vitest";
import { sshGitWorkspaceArchiveExcludes, sshGitWorkspaceIgnoredExcludes } from "./ssh.js";

// PATCH-0009-gitignore-aware-archive
describe("SSH git workspace archive exclusions", () => {
  it("keeps the fixed floor: worktrees and dependency trees", () => {
    expect(sshGitWorkspaceArchiveExcludes()).toEqual([
      ".git",
      ".paperclip-runtime",
      ".paperclip/worktrees",
      "node_modules",
    ]);
  });

  it("turns git's ignored paths into tar excludes covering their contents", () => {
    expect(sshGitWorkspaceIgnoredExcludes([".next/", "common/dist/"])).toEqual([
      ".next",
      ".next/*",
      "common/dist",
      "common/dist/*",
    ]);
  });

  it("drops entries that would escape the workspace root", () => {
    expect(sshGitWorkspaceIgnoredExcludes(["../outside", "/abs", ""])).toEqual([]);
  });
});
'''
    if t != want:
        try:
            test.write_text(want)
            print('patched:' + str(test))
        except PermissionError:
            print('test not writable as this user; skipped')
    else:
        print('test already-current')
