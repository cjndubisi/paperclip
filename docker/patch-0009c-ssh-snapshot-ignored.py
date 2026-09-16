#!/usr/bin/env python3
"""PATCH-0009c: teach the SSH driver's own git snapshot to read the ignore set.

ssh.ts keeps a NARROWER local snapshot type than git-workspace-sync.ts and only
reads HEAD/branch/deleted -- it never ran `git status --ignored`. So the ignore
set the sandbox driver uses did not merely go unused on this path; on the SSH
path it was never computed at all. Add it here, mirroring the sandbox driver's
`git status --ignored --porcelain=v1 -z --untracked-files=normal` parse.
"""
from pathlib import Path
import sys

p = Path('/app/packages/adapter-utils/src/ssh.ts')
s = p.read_text()
MARKER = 'PATCH-0009c-ssh-snapshot-ignored'
if MARKER in s:
    print('already-current'); sys.exit(0)

OLD_IFACE = '''interface LocalGitWorkspaceSnapshot {
  headCommit: string;
  branchName: string | null;
  deletedPaths: string[];
}'''
NEW_IFACE = '''interface LocalGitWorkspaceSnapshot {
  headCommit: string;
  branchName: string | null;
  deletedPaths: string[];
  // PATCH-0009c-ssh-snapshot-ignored: the repo's own `git status --ignored` set,
  // used to keep gitignored build output out of the archive (both directions).
  ignoredPaths: string[];
}'''
if OLD_IFACE not in s:
    raise SystemExit('ssh.ts: PATCH-0009c interface anchor not found')
s = s.replace(OLD_IFACE, NEW_IFACE, 1)

OLD_READ = '''    const [headCommitResult, branchResult, deletedResult] = await Promise.all([
      runLocalGit(localDir, ["rev-parse", "HEAD"], {
        timeout: 10_000,
        maxBuffer: 16 * 1024,
      }),
      runLocalGit(localDir, ["rev-parse", "--abbrev-ref", "HEAD"], {
        timeout: 10_000,
        maxBuffer: 16 * 1024,
      }),
      runLocalGit(localDir, ["ls-files", "--deleted", "-z"], {
        timeout: 10_000,
        maxBuffer: 256 * 1024,
      }),
    ]);

    const branchName = branchResult.stdout.trim();
    return {
      headCommit: headCommitResult.stdout.trim(),
      branchName: branchName && branchName !== "HEAD" ? branchName : null,
      deletedPaths: deletedResult.stdout
        .split("\\0")
        .map((entry) => entry.trim())
        .filter(Boolean),
    };'''
NEW_READ = '''    const [headCommitResult, branchResult, deletedResult, ignoredResult] = await Promise.all([
      runLocalGit(localDir, ["rev-parse", "HEAD"], {
        timeout: 10_000,
        maxBuffer: 16 * 1024,
      }),
      runLocalGit(localDir, ["rev-parse", "--abbrev-ref", "HEAD"], {
        timeout: 10_000,
        maxBuffer: 16 * 1024,
      }),
      runLocalGit(localDir, ["ls-files", "--deleted", "-z"], {
        timeout: 10_000,
        maxBuffer: 256 * 1024,
      }),
      // PATCH-0009c-ssh-snapshot-ignored: mirrors the sandbox driver's ignore
      // walk. `--untracked-files=normal` reports an ignored DIRECTORY as one
      // entry instead of recursing into it, which is what keeps this cheap on a
      // tree containing a 250 MB .next. Failure is non-fatal: an empty set just
      // means we fall back to the fixed floor rather than failing the run.
      runLocalGit(localDir, ["status", "--ignored", "--porcelain=v1", "-z", "--untracked-files=normal"], {
        timeout: 20_000,
        maxBuffer: 1024 * 1024,
      }).catch(() => ({ stdout: "" })),
    ]);

    const branchName = branchResult.stdout.trim();
    return {
      headCommit: headCommitResult.stdout.trim(),
      branchName: branchName && branchName !== "HEAD" ? branchName : null,
      deletedPaths: deletedResult.stdout
        .split("\\0")
        .map((entry) => entry.trim())
        .filter(Boolean),
      ignoredPaths: ignoredResult.stdout
        .split("\\0")
        .filter((entry) => entry.startsWith("!! "))
        .map((entry) => entry.slice(3).replace(/\\/+$/, ""))
        .filter(Boolean),
    };'''
if OLD_READ not in s:
    raise SystemExit('ssh.ts: PATCH-0009c read anchor not found')
s = s.replace(OLD_READ, NEW_READ, 1)

p.write_text(s)
print('patched:' + str(p))
