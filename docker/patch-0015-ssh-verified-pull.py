#!/usr/bin/env python3
"""PATCH-0015: staged, verified, retried SSH pulls (workspace restore + git-history export).

Evidence (2026-11-16, VPS, `sprite proxy --ssh` client 2026-09-02 and 2026-11-03, box v2-pool-*):
`ssh <box> 'cat /tmp/z'` of a 3 MB or 30 MB file returned 0 bytes with ssh exit 255 and EMPTY
stderr in 11-17 of 25-30 attempts. `ssh -vvv` shows the server closing the channel right after
"exec request accepted" without sending exit-status (client logs `Exit status -1`). Small
outputs (<20 KB) never fail; it is the Sprites ssh proxy dropping the session, not Paperclip,
the router, keepalives or timeouts (PATCH-0012*). 27 runs since 2026-10-04 failed this way at
"Restoring workspace from ssh: failed after 0.0 MB" / "Exporting git history: failed".
A retry on a fresh connection succeeds (30 transfers, worst case 5 attempts).

Fix: every pull is two steps.
  1. stage:  run the producer remotely into a file under the remote dir, print `<bytes> <sha256>`
             (tiny output, never affected).
  2. fetch:  `cat` the staged file into a local temp file; accept only if size AND sha256 match.
             On ssh 255 / mismatch, retry the fetch on a new connection (max PULL_ATTEMPTS).
Nothing local is touched until a fetch verifies, so a failed pull can never half-apply.
The staged file is removed afterwards (best-effort).

Idempotent. Run as the container user `node`, never root.
"""
from pathlib import Path
import sys

p = Path('/app/packages/adapter-utils/src/ssh.ts')
if not p.exists():
    raise SystemExit('ssh.ts not found')
s = p.read_text()
MARKER = 'PATCH-0015-ssh-verified-pull'
if MARKER in s:
    print('already-current')
    sys.exit(0)


def replace_once(src: str, old: str, new: str, what: str) -> str:
    n = src.count(old)
    if n != 1:
        raise SystemExit(f'ssh.ts: PATCH-0015 anchor "{what}" found {n} times (expected 1)')
    return src.replace(old, new, 1)


# 1. imports: createHash
s = replace_once(s, 'import { randomUUID } from "node:crypto";',
                 'import { createHash, randomUUID } from "node:crypto";', 'crypto import')

# 2. helper, inserted before exportGitWorkspaceFromSsh
HELPER = r'''
// PATCH-0015-ssh-verified-pull: the Sprites ssh proxy drops ~half of large single-stream
// pulls (exit 255, 0 bytes, empty stderr). Stage remotely, then fetch + verify size/sha256,
// retrying the fetch on a fresh connection. See docker/patch-0015-ssh-verified-pull.py.
export const SSH_PULL_ATTEMPTS = 6;
export const SSH_PULL_BACKOFF_MS = [0, 500, 1_000, 2_000, 3_000, 5_000];

export function isDroppedSshPull(error: unknown): boolean {
  const e = error as { code?: unknown; message?: unknown } | null;
  if (e && e.code === 255) return true;
  const msg = e && typeof e.message === "string" ? e.message : String(error);
  return /ssh exited with code 255|unexpected stage output: ""/.test(msg);
}

export function parseStagedPullMeta(stdout: string): { bytes: number; sha256: string } {
  const line = stdout.trim().split("\n").filter((l) => l.trim()).pop() ?? "";
  const m = /^PCSTAGED (\d+) ([0-9a-f]{64})$/.exec(line.trim());
  if (!m) throw new Error(`staged pull: unexpected stage output: ${JSON.stringify(line.slice(0, 120))}`);
  return { bytes: Number(m[1]), sha256: m[2]! };
}

async function fileSha256(file: string): Promise<{ bytes: number; sha256: string }> {
  const hash = createHash("sha256");
  let bytes = 0;
  for await (const chunk of createReadStream(file)) {
    bytes += (chunk as Buffer).length;
    hash.update(chunk as Buffer);
  }
  return { bytes, sha256: hash.digest("hex") };
}

export async function pullVerifiedFromSsh(input: {
  spec: SshConnectionConfig;
  // Shell that writes the payload to stdout. Runs remotely with `set -e`.
  producerScript: string;
  stagingDir: string;
  localFile: string;
  progress?: TransferProgress;
  attempts?: number;
  sleep?: (ms: number) => Promise<void>;
  // Test seam: defaults to streamSshToLocalFile / runSshScript.
  fetch?: (args: { spec: SshConnectionConfig; remoteScript: string; localFile: string; progress?: TransferProgress }) => Promise<void>;
  run?: (spec: SshConnectionConfig, script: string) => Promise<{ stdout: string }>;
}): Promise<{ bytes: number; attempts: number }> {
  const attempts = Math.max(1, input.attempts ?? SSH_PULL_ATTEMPTS);
  const sleep = input.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const fetch = input.fetch ?? streamSshToLocalFile;
  const run = input.run ?? ((spec: SshConnectionConfig, script: string) =>
    runSshScript(spec, script, { timeoutMs: 600_000, maxBuffer: 64 * 1024 }));
  const staged = path.posix.join(input.stagingDir, `pull-${randomUUID()}.bin`);
  const q = shellQuote(staged);
  const stageScript = [
    "set -e",
    `mkdir -p ${shellQuote(input.stagingDir)}`,
    `( ${input.producerScript} ) > ${q}`,
    `printf 'PCSTAGED %s %s\\n' "$(wc -c < ${q} | tr -d ' ')" "$(sha256sum ${q} | cut -d' ' -f1)"`,
  ].join("\n");

  let lastError: unknown = null;
  let meta: { bytes: number; sha256: string } | null = null;
  try {
    // Stage: small stdout, but the connection itself can still drop -> retry too.
    for (let i = 0; i < attempts && !meta; i += 1) {
      if (i > 0) await sleep(SSH_PULL_BACKOFF_MS[Math.min(i, SSH_PULL_BACKOFF_MS.length - 1)]!);
      try {
        meta = parseStagedPullMeta((await run(input.spec, stageScript)).stdout);
      } catch (error) {
        lastError = error;
        // Only a dropped connection (ssh exit 255) or an empty stage reply is retried; a producer
        // failure (tar/git non-zero) is deterministic and surfaces immediately.
        if (!isDroppedSshPull(error)) throw error;
      }
    }
    if (!meta) throw lastError ?? new Error("staged pull: stage failed");

    for (let i = 0; i < attempts; i += 1) {
      if (i > 0) await sleep(SSH_PULL_BACKOFF_MS[Math.min(i, SSH_PULL_BACKOFF_MS.length - 1)]!);
      try {
        await fetch({ spec: input.spec, remoteScript: `cat ${q}`, localFile: input.localFile, progress: i === 0 ? input.progress : undefined });
        const got = await fileSha256(input.localFile);
        if (got.bytes === meta.bytes && got.sha256 === meta.sha256) return { bytes: got.bytes, attempts: i + 1 };
        lastError = new Error(`staged pull: verify failed (got ${got.bytes} bytes, want ${meta.bytes})`);
      } catch (error) {
        lastError = error;
      }
      await fs.rm(input.localFile, { force: true }).catch(() => undefined);
    }
    const why = lastError instanceof Error ? lastError.message : String(lastError);
    throw new Error(`staged pull failed after ${attempts} attempts: ${why}`, { cause: lastError });
  } finally {
    await run(input.spec, `rm -f ${q}`).catch(() => undefined);
  }
}

'''
s = replace_once(s, '\nasync function exportGitWorkspaceFromSsh(input: {',
                 HELPER + 'async function exportGitWorkspaceFromSsh(input: {', 'exportGitWorkspaceFromSsh decl')

# 3. git history export: bundle into the staged file instead of streaming it.
OLD_EXPORT = '''      `tmp_bundle=$(mktemp ${shellQuote(path.posix.join(input.remoteDir, ".paperclip-runtime", "export-XXXXXX.bundle"))})`,
      'cleanup() { rm -f "$tmp_bundle"; git -C ' + shellQuote(input.remoteDir) + ' update-ref -d refs/paperclip/ssh-sync/export >/dev/null 2>&1 || true; }',
      'trap cleanup EXIT',
      `git -C ${shellQuote(input.remoteDir)} bundle create "$tmp_bundle" refs/paperclip/ssh-sync/export >/dev/null`,
      'cat "$tmp_bundle"','''
NEW_EXPORT = '''      // PATCH-0015-ssh-verified-pull: the bundle is the producer's stdout; pullVerifiedFromSsh
      // stages it remotely, then fetches + verifies it with retries.
      `tmp_bundle=$(mktemp ${shellQuote(path.posix.join(input.remoteDir, ".paperclip-runtime", "export-XXXXXX.bundle"))})`,
      'cleanup() { rm -f "$tmp_bundle"; git -C ' + shellQuote(input.remoteDir) + ' update-ref -d refs/paperclip/ssh-sync/export >/dev/null 2>&1 || true; }',
      'trap cleanup EXIT',
      `git -C ${shellQuote(input.remoteDir)} bundle create "$tmp_bundle" refs/paperclip/ssh-sync/export >/dev/null`,
      'cat "$tmp_bundle"','''
s = replace_once(s, OLD_EXPORT, NEW_EXPORT, 'export script')

OLD_EXPORT_CALL = '''      await streamSshToLocalFile({
        spec: input.spec,
        remoteScript: exportScript,
        localFile: bundlePath,
        progress: progress ?? undefined,
      });'''
NEW_EXPORT_CALL = '''      await pullVerifiedFromSsh({
        spec: input.spec,
        producerScript: exportScript,
        stagingDir: path.posix.join(input.remoteDir, ".paperclip-runtime"),
        localFile: bundlePath,
        progress: progress ?? undefined,
      });'''
s = replace_once(s, OLD_EXPORT_CALL, NEW_EXPORT_CALL, 'export streamSshToLocalFile call')

# 4. workspace restore (syncDirectoryFromSsh): pull a verified tar, then extract locally.
OLD_SYNC_START = '''  try {
    await new Promise<void>((resolve, reject) => {
      const ssh = spawn("ssh", sshArgs, {
        stdio: ["ignore", "pipe", "pipe"],
      });
      const tar = spawn("tar", ["-xf", "-", "-C", stagingDir], {'''
NEW_SYNC_START = '''  try {
    // PATCH-0015-ssh-verified-pull: stage the tar remotely, fetch + verify it (retrying dropped
    // connections), then extract the verified local archive. The direct ssh|tar stream below is
    // kept only as dead code for patch-anchor stability and is never reached.
    const archiveDir = await fs.mkdtemp(path.join(os.tmpdir(), "paperclip-ssh-pull-"));
    try {
      const archive = path.join(archiveDir, "workspace.tar");
      await pullVerifiedFromSsh({
        spec: input.spec,
        producerScript: remoteTarScript,
        stagingDir: "/tmp",
        localFile: archive,
        progress: progress ?? undefined,
      });
      await new Promise<void>((resolve, reject) => {
        const tar = spawn("tar", ["-xf", archive, "-C", stagingDir], { stdio: ["ignore", "ignore", "pipe"], env: tarSpawnEnv() });
        let tarErr = "";
        tar.stderr?.on("data", (c) => { tarErr += String(c); });
        tar.on("error", reject);
        tar.on("close", (code) => (code === 0 ? resolve() : reject(new Error(tarErr.trim() || `tar exited with code ${code}`))));
      });
    } finally {
      await fs.rm(archiveDir, { recursive: true, force: true }).catch(() => undefined);
    }
    if (sshArgs.length < 0) await new Promise<void>((resolve, reject) => {
      const ssh = spawn("ssh", sshArgs, {
        stdio: ["ignore", "pipe", "pipe"],
      });
      const tar = spawn("tar", ["-xf", "-", "-C", stagingDir], {'''
s = replace_once(s, OLD_SYNC_START, NEW_SYNC_START, 'syncDirectoryFromSsh stream start')

p.write_text(s)
print('patched:' + str(p))
