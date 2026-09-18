#!/usr/bin/env python3
"""PATCH-0012: SSH keepalive on every controller->sprite ssh spawn.

Evidence (2026-09-18 Argus forensics): runs c65ec25f / 3853440f / beb0bbe5 went
silent for 3-17 minutes after a tool_execution_update, then died with
`Exporting git history from ssh: failed after 0.0 MB` -> `Pi/ssh exited with
code 255`. That is a black-holed TCP session: the Sprite/router hop vanished
and the controller-side ssh never noticed because `createSshAuthArgs`
(ssh.ts) emits only BatchMode/ConnectTimeout/StrictHostKeyChecking.
ConnectTimeout bounds the handshake only; nothing bounds an established
session. ServerAliveInterval=15 x ServerAliveCountMax=4 turns that infinite
wait into a normal ssh exit within ~60s so the driver's existing error path
(and the agent's timeoutSec) can act on it.

All six ssh spawn sites and stageSshRemoteScript reuse auth.args, so this one
anchor covers every connection.
Upgrade: re-applied on every container start; if upstream changes the anchor
the applicator exits non-zero and the container does not start (fail-closed,
same as PATCH-0007..0011). If upstream ever adds ServerAlive itself, delete
this file and its registration line.
Idempotent: re-running prints already-current.
"""
from pathlib import Path
import sys
p = Path('/app/packages/adapter-utils/src/ssh.ts')
if not p.exists():
    raise SystemExit('ssh.ts not found')
s = p.read_text()
MARKER = 'PATCH-0012-ssh-keepalive'
if MARKER in s:
    print('already-current')
    sys.exit(0)
OLD = '''    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
'''
NEW = '''    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    // PATCH-0012-ssh-keepalive: detect a black-holed session within ~60s so a
    // dropped Sprite/router socket becomes a normal ssh exit instead of a hang.
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=4",
'''
if s.count(OLD) != 1:
    raise SystemExit(f'ssh.ts: PATCH-0012 auth-args anchor count = {s.count(OLD)}, expected 1')
s = s.replace(OLD, NEW, 1)
p.write_text(s)
print('patched:' + str(p))
