#!/usr/bin/env python3
"""PATCH-0006b: remove the impossible stdout listener from upload transfers."""
from pathlib import Path
import sys

ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
if not ssh.exists():
    raise SystemExit('ssh.ts not found')
s = ssh.read_text()
MARKER = 'PATCH-0006b-upload-stdout-type'
if MARKER in s:
    print('already-current')
    sys.exit(0)
old = '''    ssh.stdin?.on("error", fail);
    ssh.stdout?.on("error", fail);

    ssh.stderr?.on("data", (chunk) => {'''
new = '''    ssh.stdin?.on("error", fail);
    // PATCH-0006b-upload-stdout-type: stdout is configured as "ignore" for
    // upload transfers, so it is null by construction and has no errors to
    // observe. Keeping this listener makes TypeScript narrow it to never.

    ssh.stderr?.on("data", (chunk) => {'''
if old not in s:
    raise SystemExit('ssh.ts: PATCH-0006b upload listener anchor not found')
ssh.write_text(s.replace(old, new, 1))
print('patched:' + str(ssh))
