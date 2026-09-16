#!/usr/bin/env python3
"""Apply durable Paperclip runtime overlays after image replacement."""
from pathlib import Path
import sys

changed = []

# Remove an abandoned live-only experiment that wrapped the Git-context probe
# in a second shell. Its command string was syntactically invalid TypeScript,
# and PATCH-0001 already fixes the underlying multiline SSH transport correctly.
execution_target = Path('/app/packages/adapter-utils/src/execution-target.ts')
if execution_target.exists():
    s = execution_target.read_text()
    bad = '''    // PATCH-0004: dash(1) does not expand \\0 in single-quoted printf format
    // strings (emits literal backslash-zero), so the NUL-framed protocol
    // markers came back as literal text and payload split failed. Invoke the
    // probe with bash, which is present on sprite images (/usr/bin/bash),
    // falling back to sh only if bash is absent: the $0 name is harmless.
    const probeShell = "command -v bash >/dev/null 2>&1 && exec bash -c "$0" "$@" || exec sh -c "$0" "$@"";
    const result = await adapterExecutionTargetCommandRunner(remote).execute({
      command: "sh", args: ["-c", probeShell, "paperclip-git-context-shell", probe, "paperclip-git-context", input.hostCredentials ? "host" : "managed"],
'''
    good = '''    const result = await adapterExecutionTargetCommandRunner(remote).execute({
      command: "sh", args: ["-c", probe, "paperclip-git-context", input.hostCredentials ? "host" : "managed"],
'''
    if bad in s:
        execution_target.write_text(s.replace(bad, good, 1))
        changed.append(str(execution_target))
    elif 'PATCH-0004' in s:
        raise SystemExit('execution-target.ts: unknown PATCH-0004 shape; refusing to continue')

# PATCH-0001 keeps every multiline SSH payload intact at the runSshCommand
# boundary. Applying it in createSshCommandManagedRuntimeRunner is too early:
# later setup/finalize callers invoke runSshCommand directly.
ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
if ssh.exists():
    s = ssh.read_text()
    misplaced_marker = 'PATCH-0001-ssh-multiline: stage a sh -c payload as a file'
    if misplaced_marker in s and 'PATCH-0001-ssh-multiline: stage the remote script to a file' not in s:
        raise SystemExit('ssh.ts: obsolete misplaced PATCH-0001 found; refuse partial repair')
    old = '''    const envArgs = envEntries.map(([key, value]) => `${key}=${shellQuote(value)}`);
    const remoteScript = [
      'if [ -f /etc/profile ]; then . /etc/profile >/dev/null 2>&1 || true; fi',
      'if [ -f "$HOME/.profile" ]; then . "$HOME/.profile" >/dev/null 2>&1 || true; fi',
      'if [ -f "$HOME/.bash_profile" ]; then . "$HOME/.bash_profile" >/dev/null 2>&1 || true; elif [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc" >/dev/null 2>&1 || true; fi',
      'if [ -f "$HOME/.zprofile" ]; then . "$HOME/.zprofile" >/dev/null 2>&1 || true; fi',
      envArgs.length > 0
        ? `exec env ${envArgs.join(" ")} sh -c ${shellQuote(remoteCommand)}`
        : `exec sh -c ${shellQuote(remoteCommand)}`,
    ].join(" && ");
'''
    new = '''    const envArgs = envEntries.map(([key, value]) => `${key}=${shellQuote(value)}`);
    // PATCH-0001-ssh-multiline: stage the remote script to a file and run it,
    // instead of interpolating it into the shell command line.
    const scriptPath = `/tmp/.paperclip-ssh-${Date.now().toString(36)}-${randomUUID()}.sh`;
    const runScript = envArgs.length > 0
      ? `env ${envArgs.join(" ")} sh ${shellQuote(scriptPath)}`
      : `sh ${shellQuote(scriptPath)}`;
    const remoteScript = [
      'if [ -f /etc/profile ]; then . /etc/profile >/dev/null 2>&1 || true; fi',
      'if [ -f "$HOME/.profile" ]; then . "$HOME/.profile" >/dev/null 2>&1 || true; fi',
      'if [ -f "$HOME/.bash_profile" ]; then . "$HOME/.bash_profile" >/dev/null 2>&1 || true; elif [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc" >/dev/null 2>&1 || true; fi',
      'if [ -f "$HOME/.zprofile" ]; then . "$HOME/.zprofile" >/dev/null 2>&1 || true; fi',
      `printf %s ${shellQuote(Buffer.from(remoteCommand, "utf8").toString("base64"))} | base64 -d > ${shellQuote(scriptPath)}`,
      `${runScript}; PCRC=$?; rm -f ${shellQuote(scriptPath)}; exit $PCRC`,
    ].join(" && ");
'''
    if 'PATCH-0001-ssh-multiline: stage the remote script to a file' not in s:
        if old not in s:
            raise SystemExit('ssh.ts: PATCH-0001 runSshCommand anchor not found')
        ssh.write_text(s.replace(old, new, 1))
        changed.append(str(ssh))

# The adapter child can emit more than one stdin error during teardown. A
# once-listener catches the first and leaves the second EPIPE process-fatal.
p = Path('/app/packages/adapter-utils/src/server-utils.ts')
if p.exists():
    s = p.read_text()
    old = '''        if (opts.stdin != null && stdin) {
          void spawnPersistPromise.finally(() => {
            if (child.killed || stdin.destroyed) return;
            stdin.write(opts.stdin as string);
            stdin.end();
          });
        }
'''
    old_once = '''        if (opts.stdin != null && stdin) {
          // PATCH-0003-child-stdin-epipe: the child may close fd 0 while
          // onSpawn persistence is pending. Contain prompt-write EPIPE at the
          // run boundary rather than crashing the entire Paperclip process.
          stdin.once("error", (err: NodeJS.ErrnoException) => {
            onLogError(err, runId, "failed to write child process stdin");
            signalRunningProcess({ child, processGroupId }, "SIGTERM");
          });
          void spawnPersistPromise.finally(() => {
            if (child.killed || stdin.destroyed) return;
            stdin.write(opts.stdin as string);
            stdin.end();
          });
        }
'''
    new = '''        if (opts.stdin != null && stdin) {
          // PATCH-0003-child-stdin-epipe: keep the listener for the full
          // stream lifetime. Teardown can emit more than one stdin error.
          stdin.on("error", (err: NodeJS.ErrnoException) => {
            onLogError(err, runId, "failed to write child process stdin");
            signalRunningProcess({ child, processGroupId }, "SIGTERM");
          });
          void spawnPersistPromise.finally(() => {
            if (child.killed || stdin.destroyed) return;
            stdin.write(opts.stdin as string);
            stdin.end();
          });
        }
'''
    if new not in s:
        source = old_once if old_once in s else old if old in s else None
        if source is None:
            raise SystemExit('server-utils.ts: expected stdin block not found')
        p.write_text(s.replace(source, new, 1))
        changed.append(str(p))

# Apply the SSH callback writer guard too. This is a separate emitter from
# server-utils and both must survive redeploys.
ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
if ssh.exists():
    s = ssh.read_text()
    old = '''    child.on("error", (error) => {
      clearTimers();
      finishReject(Object.assign(error, { code: null }));
    });

    child.on("close", (code, signal) => {
'''
    new = '''    child.on("error", (error) => {
      clearTimers();
      finishReject(Object.assign(error, { code: null }));
    });

    // PATCH-0002-ssh-stdin-epipe: an SSH process can close its input before a
    // bridge payload finishes writing. Without an error listener, Node treats
    // the resulting EPIPE as an uncaught exception and exits the whole server.
    child.stdin?.on("error", (error) => {
      child.kill("SIGTERM");
      clearTimers();
      finishReject(Object.assign(new Error(`Failed to write process stdin: ${error.message}`, {
        cause: error,
      }), { code: null }));
    });
    child.on("close", (code, signal) => {
'''
    if 'PATCH-0002-ssh-stdin-epipe' not in s:
        if old not in s:
            raise SystemExit('ssh.ts: expected child error/close block not found')
        ssh.write_text(s.replace(old, new, 1))
        changed.append(str(ssh))

# PATCH-0005 contains plugin-worker JSON-RPC stdin failures at the worker
# boundary. A `writable` check is not sufficient: the worker can close fd 0
# after the check, and stream write errors are emitted asynchronously.
for manager_path in (
    Path('/app/server/src/services/plugin-worker-manager.ts'),
    Path('/app/server/dist/services/plugin-worker-manager.js'),
):
    if not manager_path.exists():
        continue
    s = manager_path.read_text()
    if 'PATCH-0005-plugin-worker-stdin-epipe' in s:
        continue
    if manager_path.suffix == '.ts':
        old = '''    // The `error` listener stops an unhandled EPIPE from a child that closed its
    // stdin.
    if (child.stdin) {
      child.stdin.on("error", () => {});
      child.stdin.on("close", () => {});
    }
'''
        new = '''    // PATCH-0005-plugin-worker-stdin-epipe: contain async stream failures at
    // the worker boundary. `stdin.writable` can remain true after the peer has
    // closed fd 0, so the error must stay observed for the stream lifetime.
    if (child.stdin) {
      child.stdin.on("error", (err: NodeJS.ErrnoException) => {
        log.warn({ err: err.message, code: err.code }, "worker stdin write failed");
        rejectAllPending(
          new Error(formatWorkerFailureMessage(
            `Worker stdin write failed: ${err.message}`,
            stderrExcerpt,
          )),
        );
      });
      child.stdin.on("close", () => {});
    }
'''
    else:
        old = '''        // The `error` listener stops an unhandled EPIPE from a child that closed its
        // stdin.
        if (child.stdin) {
            child.stdin.on("error", () => { });
            child.stdin.on("close", () => { });
        }
'''
        new = '''        // PATCH-0005-plugin-worker-stdin-epipe: contain async stream failures at
        // the worker boundary. `stdin.writable` can remain true after the peer has
        // closed fd 0, so the error must stay observed for the stream lifetime.
        if (child.stdin) {
            child.stdin.on("error", (err) => {
                log.warn({ err: err.message, code: err.code }, "worker stdin write failed");
                rejectAllPending(new Error(formatWorkerFailureMessage(`Worker stdin write failed: ${err.message}`, stderrExcerpt)));
            });
            child.stdin.on("close", () => { });
        }
'''
    if old not in s:
        raise SystemExit(f'{manager_path}: PATCH-0005 stdin handler anchor not found')
    manager_path.write_text(s.replace(old, new, 1))
    changed.append(str(manager_path))

# PATCH-0006: direct Git bundle streams bypass runSshCommand. Stage the
# script from an argv-carried base64 value, never from binary transfer stdin.
ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
if ssh.exists():
    s = ssh.read_text()
    marker = '// PATCH-0006-ssh-transfer-script'
    if marker not in s:
        anchor = 'async function streamLocalFileToSsh(input: {'
        helper = '''// PATCH-0006-ssh-transfer-script
function buildSshTransferCommand(script: string): string {
  const scriptPath = `/tmp/.paperclip-transfer-${randomUUID()}.sh`;
  const quotedPath = shellQuote(scriptPath);
  const encoded = shellQuote(Buffer.from(script, "utf8").toString("base64"));
  // The pipeline only consumes printf output. The child script inherits the
  // original stdin, including every byte of a streamed Git bundle.
  const command = `umask 077; printf %s ${encoded} | base64 -d > ${quotedPath} && sh ${quotedPath}; PCRC=$?; rm -f ${quotedPath}; exit $PCRC`;
  return `sh -c ${shellQuote(command)}`;
}

'''
        old = '`sh -c ${shellQuote(input.remoteScript)}`'
        if anchor not in s or s.count(old) != 2:
            raise SystemExit('ssh.ts: PATCH-0006 transfer script anchors changed')
        s = s.replace(anchor, helper + anchor, 1).replace(old, 'buildSshTransferCommand(input.remoteScript)')
        ssh.write_text(s)
        changed.append(str(ssh))

# Keep pipe errors local to the transfer, and wait for stderr to drain before
# rejecting. ChildProcess 'error' does not observe its stdin/stdout sockets.
if ssh.exists():
    s = ssh.read_text()
    if '// PATCH-0006-transfer-stream-guards' not in s:
        sections = [
            ('streamLocalFileToSsh', 'streamSshToLocalFile', '    ', ['ssh'], 'source.destroy();', ['ssh.stdin'], 'input.progress', 'sshStderr'),
            ('streamSshToLocalFile', 'importGitWorkspaceToSsh', '    ', ['ssh'], 'sink.destroy();', ['ssh.stdout'], 'input.progress', 'sshStderr'),
            ('syncDirectoryToSsh', 'syncDirectoryFromSsh', '    ', ['tar', 'ssh'], '', ['ssh.stdin', 'tar.stdout'], 'progress', 'sshStderr + "\\n" + tarStderr'),
            ('syncDirectoryFromSsh', 'prepareWorkspaceForSshExecution', '      ', ['ssh', 'tar'], '', ['tar.stdin', 'ssh.stdout'], 'progress', 'tarStderr + "\\n" + sshStderr'),
        ]
        for name, following, indent, children, cleanup, streams, progress, stderr in sections:
            start = s.index('async function ' + name + '(')
            end = s.index('async function ' + following + '(', start)
            part = s[start:end]
            a = part.index(indent + 'const fail = (error: Error) => {')
            b = part.index(indent + '};', a) + len(indent + '};')
            lines = [
                '// PATCH-0006-transfer-stream-guards',
                'const fail = (error: Error) => {',
                '  if (settled) return;',
                '  settled = true;',
                '  const closed = Promise.all([' + ', '.join('new Promise<void>((done) => { if (' + c + '.exitCode !== null || ' + c + '.signalCode !== null) done(); else ' + c + '.once("close", () => done()); })' for c in children) + ']);',
                '  ' + cleanup,
                '  ' + progress + '?.counter.destroy();',
            ]
            lines += ['  ' + c + '.kill("SIGTERM");' for c in children]
            lines += ['  void closed.then(() => reject(new Error(`SSH transfer failed: ${error.message}; ${(' + stderr + ').trim()}`, { cause: error })));', '};']
            lines += [stream + '?.on("error", fail);' for stream in streams]
            part = part[:a] + '\n'.join(indent + line for line in lines) + part[b:]
            s = s[:start] + part + s[end:]
        ssh.write_text(s)
        changed.append(str(ssh))

# PATCH-0006b: repair the upload-path type regression carried by the first 0006
# overlay without changing its runtime behaviour (stdout is ignored/null).
patch_0006b = Path('/opt/paperclip-overlays/patch-0006b-upload-stdout-type.py')
if not patch_0006b.exists():
    raise SystemExit('PATCH-0006b helper missing from deployment image')
namespace = {'__name__': '__main__'}
exec(compile(patch_0006b.read_text(), str(patch_0006b), 'exec'), namespace)

# PATCH-0007: stage oversized adapter launch scripts so the Sprite SSH server's
# ~65 KB exec-request ceiling cannot surface as a false Pi exit 255.
patch_0007 = Path('/opt/paperclip-overlays/patch-0007-ssh-exec-command-limit.py')
if not patch_0007.exists():
    raise SystemExit('PATCH-0007 helper missing from deployment image')
namespace = {'__name__': '__main__'}
exec(compile(patch_0007.read_text(), str(patch_0007), 'exec'), namespace)

# PATCH-0008: shared Git workspaces contain Paperclip's sibling worktrees and
# ignored dependency installs. They are not part of the selected run snapshot.
patch_0008 = Path('/opt/paperclip-overlays/patch-0008-ssh-workspace-excludes.py')
if not patch_0008.exists():
    raise SystemExit('PATCH-0008 helper missing from deployment image')
namespace = {'__name__': '__main__'}
exec(compile(patch_0008.read_text(), str(patch_0008), 'exec'), namespace)

print('patched:' + ','.join(changed) if changed else 'already-current')
