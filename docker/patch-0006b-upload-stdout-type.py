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
if old in s:
    ssh.write_text(s.replace(old, new, 1))
    print('patched:' + str(ssh))
    sys.exit(0)

# A fresh image with the corrected PATCH-0006 generator never had the invalid
# stdout listener. That is already the desired state.
upload_start = s.index('async function streamLocalFileToSsh(input: {')
upload_end = s.index('async function streamSshToLocalFile(input: {', upload_start)
upload = s[upload_start:upload_end]
if 'ssh.stdin?.on("error", fail);' in upload and 'ssh.stdout?.on("error", fail);' not in upload:
    print('already-current')
    sys.exit(0)
raise SystemExit('ssh.ts: PATCH-0006b upload listener anchor not found')
