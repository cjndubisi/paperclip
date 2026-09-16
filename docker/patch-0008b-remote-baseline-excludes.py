#!/usr/bin/env python3
"""PATCH-0008b: align remote baseline capture with SSH archive exclusions."""
from pathlib import Path
import sys

p = Path('/app/packages/adapter-utils/src/remote-managed-runtime.ts')
if not p.exists():
    raise SystemExit('remote-managed-runtime.ts not found')
s = p.read_text()
MARKER = 'PATCH-0008b-remote-baseline-excludes'
if MARKER in s:
    print('already-current')
    sys.exit(0)

OLD_IMPORT = '''  restoreWorkspaceFromSshExecution,
  syncDirectoryToSsh,
} from "./ssh.js";'''
NEW_IMPORT = '''  restoreWorkspaceFromSshExecution,
  sshGitWorkspaceArchiveExcludes,
  syncDirectoryToSsh,
} from "./ssh.js";'''
if OLD_IMPORT not in s:
    raise SystemExit('remote-managed-runtime.ts: PATCH-0008b import anchor not found')
s = s.replace(OLD_IMPORT, NEW_IMPORT, 1)

ANCHOR = 'export interface RemoteManagedRuntimeAsset {'
HELPER = '''// PATCH-0008b-remote-baseline-excludes: baseline capture must prune the
// same sibling worktrees and dependency installs omitted by the upload archive.
// Otherwise it can spend minutes walking 2+ GB after upload has already finished.
export function remoteManagedGitWorkspaceExcludes(): string[] {
  return sshGitWorkspaceArchiveExcludes();
}

'''
if ANCHOR not in s:
    raise SystemExit('remote-managed-runtime.ts: PATCH-0008b helper anchor not found')
s = s.replace(ANCHOR, HELPER + ANCHOR, 1)
OLD = '          ? [...GIT_ARCHIVE_EXCLUDES, ".paperclip-runtime"]'
NEW = '          ? remoteManagedGitWorkspaceExcludes()'
if OLD not in s:
    raise SystemExit('remote-managed-runtime.ts: PATCH-0008b baseline anchor not found')
s = s.replace(OLD, NEW, 1)
p.write_text(s)
print('patched:' + str(p))
