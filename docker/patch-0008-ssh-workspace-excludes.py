#!/usr/bin/env python3
"""PATCH-0008: exclude sibling worktrees and dependency trees from SSH workspace sync."""
from pathlib import Path
import sys

ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
if not ssh.exists():
    raise SystemExit('ssh.ts not found')

s = ssh.read_text()
MARKER = 'PATCH-0008-ssh-workspace-excludes'
if MARKER in s:
    print('already-current')
    sys.exit(0)

OLD_HELPER = '''function tarExcludeArgs(exclude: string[] | undefined): string[] {
  const combined = ["._*", ...(exclude ?? [])];
  return combined.flatMap((entry) => ["--exclude", entry]);
}'''
NEW_HELPER = '''function tarExcludeArgs(exclude: string[] | undefined): string[] {
  const combined = ["._*", ...(exclude ?? [])];
  return combined.flatMap((entry) => ["--exclude", entry]);
}

// PATCH-0008-ssh-workspace-excludes: a shared project checkout may contain
// Paperclip's sibling worktrees plus ignored package-manager installs. Neither
// belongs in one run's remote snapshot; archiving them inflated a 62 MB repo to
// >2 GB and raced directories that other runs were mutating.
export function sshGitWorkspaceArchiveExcludes(): string[] {
  return [".git", ".paperclip-runtime", ".paperclip/worktrees", "node_modules"];
}'''
if OLD_HELPER not in s:
    raise SystemExit('ssh.ts: PATCH-0008 tar exclusion helper anchor not found')
s = s.replace(OLD_HELPER, NEW_HELPER, 1)

OLD_EXCLUDES = '      exclude: [".git", ".paperclip-runtime"],'
if s.count(OLD_EXCLUDES) != 2:
    raise SystemExit(f'ssh.ts: PATCH-0008 expected 2 git workspace exclusion sites, found {s.count(OLD_EXCLUDES)}')
s = s.replace(OLD_EXCLUDES, '      exclude: sshGitWorkspaceArchiveExcludes(),')

ssh.write_text(s)
print('patched:' + str(ssh))
