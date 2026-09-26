#!/usr/bin/env python3
"""PATCH-0012c: give the lease-acquisition probe a timeout above ConnectTimeout.

Evidence (2026-09-26, Argus): run 693ceaa0 failed `lease_acquire_failed` on
`Role: QA` after 17.6s; a5dcb38a/cde3fe15 the same at 19.4s/22.7s. The error
object carried rc 255, `killed: true` and EMPTY stdout/stderr -- the signature of
Node's own `execFileText` timer SIGTERMing the ssh child, NOT a remote refusal
(a host that refuses, fails DNS, or times out its banner always writes stderr).

`ensureSshWorkspaceReady` calls `runSshCommand(config, "mkdir -p && cd && pwd")`
with NO options, so it inherited `options.timeoutMs ?? 15_000`. That 15s Node
timer is the binding constraint and it undercuts PATCH-0012b's
`ConnectTimeout=60`: the process is killed at 15s, so ssh never gets to use its
60s banner budget. Measured through the real proxy on an idle sprite: a cold
resume costs ~21.5s (warm: ~150ms), so 21.5s against a 15s timer is a
deterministic failure. Lease acquisition is one-shot and not retried, so one
cold wake kills the run.

Fix: pass an explicit 90s timeoutMs at the probe's own call site. 90 > the 60s
ConnectTimeout, so ssh's own bound stays the one that decides a genuinely dead
host, and the Node timer goes back to being a backstop instead of the trigger.
A warm box still returns in ~150ms, so this costs nothing on the happy path.

Narrow by construction: `ensureSshWorkspaceReady` is used only by the lease /
environment-probe paths (environment-runtime.js:675, environment-probe.js:162),
so no bulk-transfer or agent-exec timeout changes.

Idempotent. Run as the container user `node`, never root.
"""
from pathlib import Path
import sys

p = Path('/app/packages/adapter-utils/src/ssh.ts')
if not p.exists():
    raise SystemExit('ssh.ts not found')
s = p.read_text()
MARKER = 'PATCH-0012c-lease-probe-timeout'
if MARKER in s:
    print('already-current')
    sys.exit(0)

OLD = '''export async function ensureSshWorkspaceReady(
  config: SshConnectionConfig,
): Promise<{ remoteCwd: string }> {
  const result = await runSshCommand(
    config,
    `mkdir -p ${shellQuote(config.remoteWorkspacePath)} && cd ${shellQuote(config.remoteWorkspacePath)} && pwd`,
  );'''

NEW = '''export async function ensureSshWorkspaceReady(
  config: SshConnectionConfig,
): Promise<{ remoteCwd: string }> {
  const result = await runSshCommand(
    config,
    `mkdir -p ${shellQuote(config.remoteWorkspacePath)} && cd ${shellQuote(config.remoteWorkspacePath)} && pwd`,
    // PATCH-0012c-lease-probe-timeout: the default 15s kill undercut
    // ConnectTimeout=60, so a cold sprite (~21.5s to banner) died at 15s with
    // rc 255 and empty stderr. Keep this above ConnectTimeout so ssh's own
    // bound decides a dead host; lease acquisition is not retried.
    { timeoutMs: 90_000 },
  );'''

if OLD not in s:
    raise SystemExit('ssh.ts: PATCH-0012c anchor not found (ensureSshWorkspaceReady shape changed)')

p.write_text(s.replace(OLD, NEW, 1))
print('patched:' + str(p))
