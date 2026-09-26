#!/usr/bin/env python3
"""PATCH-0012d: give clearRemoteDirectory a timeout that survives a slow sprite.

Evidence (2026-09-26, Argus): run c1b70aab failed `adapter_failed` after ~34s
with rc 255, `killed: true` and EMPTY stderr -- Node's `execFileText` timer
SIGTERMing the ssh child. The command's base64 payload decoded to
`find <remoteDir> -mindepth 1 -maxdepth 1 ... -exec rm -rf -- {} +`, i.e.
`clearRemoteDirectory`, which hardcodes `timeoutMs: 30_000`. A sprite that is
still waking (cold boot measured at 97.7s) or slow on a large workspace blows
through 30s. Same class of bug as PATCH-0012c, different call site.

Fix: raise that one call site to 120s. `removeDeletedPathsOnSsh` also uses
30_000 and is deliberately left alone (it runs after the box is already awake).

Idempotent. Run as the container user `node`, never root.
"""
from pathlib import Path
import sys

p = Path('/app/packages/adapter-utils/src/ssh.ts')
if not p.exists():
    raise SystemExit('ssh.ts not found')
s = p.read_text()
MARKER = 'PATCH-0012d-clear-remote-dir-timeout'
if MARKER in s:
    print('already-current')
    sys.exit(0)

OLD = '''    `find ${shellQuote(input.remoteDir)} -mindepth 1 -maxdepth 1 ${preservePatterns} -exec rm -rf -- {} +`,
  ].join("\\n");
  await runSshScript(input.spec, script, {
    timeoutMs: 30_000,'''

NEW = '''    `find ${shellQuote(input.remoteDir)} -mindepth 1 -maxdepth 1 ${preservePatterns} -exec rm -rf -- {} +`,
  ].join("\\n");
  await runSshScript(input.spec, script, {
    // PATCH-0012d-clear-remote-dir-timeout: 30s killed a slow/waking sprite
    // mid-clear (rc 255, killed=true, empty stderr). 120s covers it.
    timeoutMs: 120_000,'''

if s.count(OLD) != 1:
    raise SystemExit('ssh.ts: PATCH-0012d anchor not found exactly once (clearRemoteDirectory shape changed)')

p.write_text(s.replace(OLD, NEW, 1))
print('patched:' + str(p))
