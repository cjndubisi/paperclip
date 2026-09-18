#!/usr/bin/env python3
"""PATCH-0011: make the remote repo ignore `.paperclip-runtime` so an agent's
own git hygiene cannot delete the live Pi session file.

Mechanism (verified on 8 Argus runs, 2026-09-18):
  1. The SSH driver materialises the run's repo on the box with `git init` +
     `fetch` + `checkout` (importGitWorkspaceToSsh). Nothing writes
     `.git/info/exclude`, so `.paperclip-runtime/` inside the checkout is an
     ordinary UNTRACKED directory as far as git is concerned.
  2. Pi appends every turn to
     `<workspace>/.paperclip-runtime/pi/sessions/<ts>-<agent>.jsonl`.
  3. The agent runs `git stash -u` / `git stash push -u` / `git clean -fd`
     (routine before a `gh pr checkout`). `-u` stashes untracked files, which
     REMOVES `.paperclip-runtime/pi/sessions/*.jsonl` from disk.
  4. Pi's next append fails: `ENOENT ... pi/sessions/<file>.jsonl`, the run
     dies as `adapter_failed`, the retry lands on a fresh box and repeats.

The driver's own `git clean -fdx -e .paperclip-runtime` already knows this
directory must survive; it just never told git in a way the AGENT's commands
would honour. `.git/info/exclude` is repo-local and untracked, so it does not
touch the project's `.gitignore` or any commit. `git stash -u` and
`git clean -fd` both honour it (verified locally); only `clean -fdx` /
`stash -a` bypass it, and those are not "routine".

Idempotent: re-running prints already-current.
"""
from pathlib import Path
import sys

p = Path('/app/packages/adapter-utils/src/ssh.ts')
if not p.exists():
    raise SystemExit('ssh.ts not found')
s = p.read_text()
MARKER = 'PATCH-0011-ssh-runtime-git-exclude'
if MARKER in s:
    print('already-current')
    sys.exit(0)

OLD = '''      `if [ ! -d ${shellQuote(path.posix.join(input.remoteDir, ".git"))} ]; then git init ${shellQuote(input.remoteDir)} >/dev/null; fi`,
'''
NEW = '''      `if [ ! -d ${shellQuote(path.posix.join(input.remoteDir, ".git"))} ]; then git init ${shellQuote(input.remoteDir)} >/dev/null; fi`,
      // PATCH-0011-ssh-runtime-git-exclude: the run's live Pi session file and
      // callback bridge live under <workspace>/.paperclip-runtime. Without a
      // repo-local exclude, `git stash -u` / `git clean -fd` from the agent
      // deletes them mid-run and Pi dies with ENOENT on its next append.
      `mkdir -p ${shellQuote(path.posix.join(input.remoteDir, ".git", "info"))}`,
      `grep -qxF '/.paperclip-runtime/' ${shellQuote(path.posix.join(input.remoteDir, ".git", "info", "exclude"))} 2>/dev/null || printf '%s\\\\n' '/.paperclip-runtime/' >> ${shellQuote(path.posix.join(input.remoteDir, ".git", "info", "exclude"))}`,
'''
if s.count(OLD) != 1:
    raise SystemExit(f'ssh.ts: PATCH-0011 git-init anchor count = {s.count(OLD)}, expected 1')
s = s.replace(OLD, NEW, 1)
p.write_text(s)
print('patched:' + str(p))
