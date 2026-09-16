#!/usr/bin/env python3
"""PATCH-0007: stage oversized SSH adapter launches."""
from pathlib import Path
import sys

ssh = Path('/app/packages/adapter-utils/src/ssh.ts')
if not ssh.exists():
    raise SystemExit('ssh.ts not found')
s = ssh.read_text()
MARKER = 'PATCH-0007-ssh-exec-command-limit'
if MARKER in s:
    print('already-current')
    sys.exit(0)

HELPER_ANCHOR = 'export async function buildSshSpawnTarget(input: {'
HELPER = '''// PATCH-0007b-ssh-exec-limit-seam
export function buildSshRemoteLaunchScript(input: {
  remoteCwd: string;
  envArgs: string[];
  remoteCommandParts: string;
}): string {
  return [
    'if [ -f /etc/profile ]; then . /etc/profile >/dev/null 2>&1 || true; fi',
    'if [ -f "$HOME/.profile" ]; then . "$HOME/.profile" >/dev/null 2>&1 || true; fi',
    'if [ -f "$HOME/.bash_profile" ]; then . "$HOME/.bash_profile" >/dev/null 2>&1 || true; elif [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc" >/dev/null 2>&1 || true; fi',
    'if [ -f "$HOME/.zprofile" ]; then . "$HOME/.zprofile" >/dev/null 2>&1 || true; fi',
    `cd ${shellQuote(input.remoteCwd)}`,
    input.envArgs.length > 0
      ? `exec env ${input.envArgs.join(" ")} ${input.remoteCommandParts}`
      : `exec ${input.remoteCommandParts}`,
  ].join(" && ");
}

export const SSH_EXEC_COMMAND_LIMIT_BYTES = 60_000;
const SSH_EXEC_STAGE_CHUNK_CHARS = 45_000;

export function sshLaunchRequiresRemoteStaging(remoteScript: string): boolean {
  return Buffer.byteLength(`sh -c ${shellQuote(remoteScript)}`, "utf8") > SSH_EXEC_COMMAND_LIMIT_BYTES;
}

function dirnamePosix(filePath: string): string {
  const index = filePath.lastIndexOf("/");
  return index > 0 ? filePath.slice(0, index) : "/";
}

async function runSshStagingCommand(input: {
  spec: SshRemoteExecutionSpec;
  sshArgs: string[];
  command: string;
  description: string;
}): Promise<void> {
  try {
    await execFileText("ssh", [
      ...input.sshArgs,
      "-p",
      String(input.spec.port),
      `${input.spec.username}@${input.spec.host}`,
      `sh -c ${shellQuote(input.command)}`,
    ], { timeout: 60_000, maxBuffer: 1024 * 64 });
  } catch (error) {
    const stderr = String((error as NodeJS.ErrnoException & { stderr?: string }).stderr ?? "").trim();
    throw new Error(`${input.description}: ${(error as Error).message}${stderr ? `; ${stderr}` : ""}`, { cause: error });
  }
}

async function stageSshRemoteScript(input: {
  spec: SshRemoteExecutionSpec;
  sshArgs: string[];
  script: string;
}): Promise<string> {
  const encoded = Buffer.from(input.script, "utf8").toString("base64");
  const base = input.spec.remoteCwd?.trim()
    ? `${input.spec.remoteCwd.replace(/\\/+$/, "")}/.paperclip-ssh-stage-${randomUUID()}`
    : `/tmp/.paperclip-ssh-stage-${randomUUID()}`;
  const b64Path = `${base}.b64`;
  const scriptPath = `${base}.sh`;
  const quotedB64 = shellQuote(b64Path);
  const quotedScript = shellQuote(scriptPath);
  for (let offset = 0; offset < encoded.length; offset += SSH_EXEC_STAGE_CHUNK_CHARS) {
    const chunk = encoded.slice(offset, offset + SSH_EXEC_STAGE_CHUNK_CHARS);
    const redirect = offset === 0 ? ">" : ">>";
    await runSshStagingCommand({
      spec: input.spec,
      sshArgs: input.sshArgs,
      command: `umask 077; mkdir -p ${shellQuote(dirnamePosix(b64Path))} && printf %s ${shellQuote(chunk)} ${redirect} ${quotedB64}`,
      description: "Failed to stage SSH command payload",
    });
  }
  await runSshStagingCommand({
    spec: input.spec,
    sshArgs: input.sshArgs,
    command: `base64 -d < ${quotedB64} > ${quotedScript} && rm -f ${quotedB64}`,
    description: "Failed to decode staged SSH command payload",
  });
  return scriptPath;
}

// PATCH-0007-ssh-exec-command-limit
'''
if HELPER_ANCHOR not in s:
    raise SystemExit('ssh.ts: PATCH-0007 buildSshSpawnTarget anchor not found')
s = s.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR, 1)

OLD_SIG = '''}): Promise<{
  command: string;
  args: string[];
  cleanup: () => Promise<void>;
}> {'''
NEW_SIG = '''}): Promise<{
  command: string;
  args: string[];
  stagedRemoteScriptPath?: string;
  cleanup: () => Promise<void>;
}> {'''
start = s.index(HELPER_ANCHOR)
sig_at = s.find(OLD_SIG, start)
if sig_at < 0:
    raise SystemExit('ssh.ts: PATCH-0007 signature anchor not found')
s = s[:sig_at] + s[sig_at:].replace(OLD_SIG, NEW_SIG, 1)

OLD_BUILD = '''  const remoteScript = [
    'if [ -f /etc/profile ]; then . /etc/profile >/dev/null 2>&1 || true; fi',
    'if [ -f "$HOME/.profile" ]; then . "$HOME/.profile" >/dev/null 2>&1 || true; fi',
    'if [ -f "$HOME/.bash_profile" ]; then . "$HOME/.bash_profile" >/dev/null 2>&1 || true; elif [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc" >/dev/null 2>&1 || true; fi',
    'if [ -f "$HOME/.zprofile" ]; then . "$HOME/.zprofile" >/dev/null 2>&1 || true; fi',
    `cd ${shellQuote(input.spec.remoteCwd)}`,
    envArgs.length > 0
      ? `exec env ${envArgs.join(" ")} ${remoteCommandParts}`
      : `exec ${remoteCommandParts}`,
  ].join(" && ");'''
NEW_BUILD = '''  const remoteScript = buildSshRemoteLaunchScript({
    remoteCwd: input.spec.remoteCwd,
    envArgs,
    remoteCommandParts,
  });'''
if OLD_BUILD not in s:
    raise SystemExit('ssh.ts: PATCH-0007 remote script anchor not found')
s = s.replace(OLD_BUILD, NEW_BUILD, 1)

OLD_TAIL = '''  sshArgs.push(
    "-p",
    String(input.spec.port),
    `${input.spec.username}@${input.spec.host}`,
    `sh -c ${shellQuote(remoteScript)}`,
  );

  return {
    command: "ssh",
    args: sshArgs,
    cleanup: auth.cleanup,
  };
}'''
NEW_TAIL = '''  const directExecCommand = `sh -c ${shellQuote(remoteScript)}`;
  if (!sshLaunchRequiresRemoteStaging(remoteScript)) {
    sshArgs.push("-p", String(input.spec.port), `${input.spec.username}@${input.spec.host}`, directExecCommand);
    return { command: "ssh", args: sshArgs, cleanup: auth.cleanup };
  }

  let stagedScriptPath: string;
  try {
    stagedScriptPath = await stageSshRemoteScript({ spec: input.spec, sshArgs: [...auth.args], script: remoteScript });
  } catch (error) {
    await auth.cleanup();
    throw error;
  }
  const quotedStagedPath = shellQuote(stagedScriptPath);
  // PATCH-0007c-staged-exec-shape
  const runStagedCommand = `sh -c ${shellQuote(`exec 3< ${quotedStagedPath}; rm -f ${quotedStagedPath}; exec sh /dev/fd/3`)}`;
  sshArgs.push("-p", String(input.spec.port), `${input.spec.username}@${input.spec.host}`, runStagedCommand);
  return { command: "ssh", args: sshArgs, stagedRemoteScriptPath: stagedScriptPath, cleanup: auth.cleanup };
}'''
if OLD_TAIL not in s:
    raise SystemExit('ssh.ts: PATCH-0007 tail anchor not found')
s = s.replace(OLD_TAIL, NEW_TAIL, 1)
ssh.write_text(s)
print('patched:' + str(ssh))
