#!/usr/bin/env python3
"""PATCH-0012b: ConnectTimeout 10 -> 60 on every controller->sprite ssh spawn.

Evidence (2026-09-26, REM-244): two Argus runs failed `lease_acquire_failed` on
`Role: QA` with rc 255 and EMPTY stderr, ~22s each, straddling the sprite's
cold boot (uptime -s 03:21:19; attempts 03:20:43 and 03:21:13). The box had
slept since 21:25. Lease acquisition is a one-shot `mkdir -p && pwd`; it is not
retried, so one slow wake kills the run.

ssh's ConnectTimeout covers the TCP connect AND the wait for the server banner.
With `sprite proxy --ssh` as ProxyCommand the banner only arrives once the VM is
up. Measured through the real proxy: a 15s banner delay -> rc 255 "Connection
timed out during banner exchange" at exactly 10s; with ConnectTimeout=60 a 45s
delay connects fine. A warm box still connects in ~150ms, so the larger bound
only costs time on a genuinely dead host.

Must run AFTER PATCH-0012 (same createSshAuthArgs array). Idempotent.
Run as the container user `node`, never root.
"""
from pathlib import Path
import sys

p = Path('/app/packages/adapter-utils/src/ssh.ts')
if not p.exists():
    raise SystemExit('ssh.ts not found')
s = p.read_text()
MARKER = 'PATCH-0012b-ssh-connect-timeout'
if MARKER in s:
    print('already-current')
    sys.exit(0)

OLD = '''    "-o",
    "ConnectTimeout=10",
    // PATCH-0012-ssh-keepalive:'''
NEW = '''    "-o",
    // PATCH-0012b-ssh-connect-timeout: a cold sprite only sends its banner once
    // the VM boots (>10s), and lease acquisition is not retried.
    "ConnectTimeout=60",
    // PATCH-0012-ssh-keepalive:'''
if OLD not in s:
    raise SystemExit('ssh.ts: PATCH-0012b anchor not found (PATCH-0012 must be applied first)')
p.write_text(s.replace(OLD, NEW, 1))
print('patched:' + str(p))
