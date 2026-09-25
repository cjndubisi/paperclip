#!/usr/bin/env python3
"""PATCH-0007d: size the SSH exec limit by its percent-encoded wire size.

PATCH-0007 stages a launch when `sh -c <script>` exceeds 60,000 RAW bytes. But
`sprite proxy --ssh` forwards the exec command percent-encoded in a URL, and the
~65.3 KB refusal applies to the ENCODED form. Every byte outside [A-Za-z0-9-_.~]
costs 3, so a Pi launch full of quotes/JSON/newlines is refused at ~46 KB raw.

Measured on role-qa 2026-09-25: 65,260 raw `x` -> rc 0; 23,036 raw `{}` (69,072
encoded) -> rc 255 with empty stderr. REM-243's Argus launches were 45.9-46.3 KB
raw / ~66 KB encoded: under the raw threshold, so sent direct, refused, and every
run died as "Pi exited with code 255" with a zero-byte session file.

Idempotent. Run as the container user `node`, never root.
"""
from pathlib import Path
import sys

ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
if not ssh.exists():
    raise SystemExit('ssh.ts not found')

s = ssh.read_text()
MARKER = 'PATCH-0007d-ssh-exec-wire-size'
if MARKER in s:
    print('already-current')
    sys.exit(0)

OLD = '''const SSH_EXEC_STAGE_CHUNK_CHARS = 45_000;

export function sshLaunchRequiresRemoteStaging(remoteScript: string): boolean {
  return Buffer.byteLength(`sh -c ${shellQuote(remoteScript)}`, "utf8") > SSH_EXEC_COMMAND_LIMIT_BYTES;
}'''

NEW = '''const SSH_EXEC_STAGE_CHUNK_CHARS = 30_000;

// PATCH-0007d-ssh-exec-wire-size: `sprite proxy --ssh` forwards the exec command
// PERCENT-ENCODED in a URL, and the ~65.3 KB refusal applies to that encoded
// form, not the raw bytes. Every byte outside [A-Za-z0-9-_.~] costs 3 on the
// wire, so a prompt full of quotes/spaces/newlines (each `'` also expands to
// `'"'"'` under shellQuote) blows the limit at ~46 KB raw. Measured on role-qa:
// 65,260 raw `x` passes, 23,036 raw `{}` (69,072 encoded) is refused -> rc 255,
// empty stderr, Pi never starts. Measure the conservative encoded size.
export function sshExecCommandWireBytes(command: string): number {
  let size = 0;
  for (const byte of Buffer.from(command, "utf8")) {
    const unreserved =
      (byte >= 0x30 && byte <= 0x39) ||
      (byte >= 0x41 && byte <= 0x5a) ||
      (byte >= 0x61 && byte <= 0x7a) ||
      byte === 0x2d || byte === 0x2e || byte === 0x5f || byte === 0x7e;
    size += unreserved ? 1 : 3;
  }
  return size;
}

export function sshLaunchRequiresRemoteStaging(remoteScript: string): boolean {
  return sshExecCommandWireBytes(`sh -c ${shellQuote(remoteScript)}`) > SSH_EXEC_COMMAND_LIMIT_BYTES;
}'''

if OLD not in s:
    raise SystemExit('ssh.ts: PATCH-0007d anchor not found (PATCH-0007 must be applied first)')

ssh.write_text(s.replace(OLD, NEW, 1))
print('patched:' + str(ssh))
