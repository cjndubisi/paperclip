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

Fix: pass an explicit 180s timeoutMs at the probe's own call site. 180 > the
60s ConnectTimeout, so the Node timer is a backstop, not the trigger.

Revision (2026-09-26): 90s was not enough. A COLD sprite (evicted, not merely
paused/warm) booted in 97.7s through the proxy (`/proc/uptime` ~97 on arrival),
and Argus then failed 4 runs in a row at ~91s each (1c943c01, def040a8,
2f4b255a, 6ca634cb). 180s covers a cold boot with margin; a dead host now takes
3 min to fail, which CJ accepted.
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
TIMEOUT = '{ timeoutMs: 180_000 },'
if MARKER in s:
    # Upgrade an in-place /app patched by the earlier 90s revision (a plain
    # restart keeps the patched /app, so the marker alone is not proof).
    start = s.index(MARKER)
    window = s[start:start + 600]
    if TIMEOUT in window:
        print('already-current')
        sys.exit(0)
    if '{ timeoutMs: 90_000 },' not in window:
        raise SystemExit('ssh.ts: PATCH-0012c marker present but timeout line not recognised')
    s = s[:start] + window.replace('{ timeoutMs: 90_000 },', TIMEOUT, 1) + s[start + 600:]
    p.write_text(s)
    print('upgraded 90s->180s:' + str(p))
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
    // ConnectTimeout=60. A warm sprite resumes in ~21s but a COLD one took
    // 97.7s to boot, so 90s also failed. Lease acquisition is not retried.
    { timeoutMs: 180_000 },
  );'''

if OLD not in s:
    raise SystemExit('ssh.ts: PATCH-0012c anchor not found (ensureSshWorkspaceReady shape changed)')

p.write_text(s.replace(OLD, NEW, 1))
print('patched:' + str(p))
