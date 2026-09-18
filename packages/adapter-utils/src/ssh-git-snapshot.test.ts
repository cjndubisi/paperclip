import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { readLocalGitWorkspaceSnapshot } from "./ssh.js";

/**
 * CHARACTERIZATION tests for the SSH driver's PRIVATE snapshot reader,
 * `readLocalGitWorkspaceSnapshot` (ssh.ts). They record what it does TODAY so
 * the planned convergence onto the shared `readGitWorkspaceSnapshot`
 * (git-workspace-sync.ts) has a real baseline to diff against instead of a
 * guess.
 *
 * TEST-ONLY EXPORT: the reader was module-private. It is now exported with an
 * `@internal` doc comment solely so this file can reach it. Its three real
 * callers (`integrateImportedGitHead`, `prepareWorkspaceForSshExecution`,
 * `restoreWorkspaceFromSshExecution`) all require a live SSH endpoint before
 * they touch the snapshot, and the SSH env lab refuses to run on darwin, so
 * driving the reader through an exported caller is not practical here. The
 * convergence deletes the function entirely, taking the export with it.
 *
 * Harness follows `git-workspace-sync.test.ts`: real `git init` repositories
 * under `os.tmpdir()`, torn down in `afterEach`.
 */

import { runLocalGit } from "./git-workspace-sync.js";

async function git(cwd: string, args: string[]): Promise<string> {
  return (await runLocalGit(cwd, args)).stdout.trim();
}

describe("readLocalGitWorkspaceSnapshot (ssh.ts private reader)", () => {
  const cleanupDirs: string[] = [];

  afterEach(async () => {
    while (cleanupDirs.length > 0) {
      const dir = cleanupDirs.pop();
      if (!dir) continue;
      await rm(dir, { recursive: true, force: true }).catch(() => undefined);
    }
  });

  async function createRepo(prefix: string): Promise<string> {
    const rootDir = await mkdtemp(path.join(os.tmpdir(), prefix));
    cleanupDirs.push(rootDir);
    const repo = path.join(rootDir, "repo");
    await mkdir(repo, { recursive: true });
    await git(repo, ["init"]);
    await git(repo, ["checkout", "-b", "main"]);
    await git(repo, ["config", "user.name", "Paperclip Test"]);
    await git(repo, ["config", "user.email", "test@paperclip.dev"]);
    await writeFile(path.join(repo, "tracked.txt"), "base\n", "utf8");
    await git(repo, ["add", "tracked.txt"]);
    await git(repo, ["commit", "-qm", "base"]);
    return repo;
  }

  it("reads headCommit and branchName from a clean repo", async () => {
    const repo = await createRepo("paperclip-ssh-snapshot-clean-");
    const expectedHead = await git(repo, ["rev-parse", "HEAD"]);

    const snapshot = await readLocalGitWorkspaceSnapshot(repo);

    expect(snapshot).not.toBeNull();
    expect(snapshot?.headCommit).toBe(expectedHead);
    expect(snapshot?.headCommit).toMatch(/^[0-9a-f]{40}$/);
    expect(snapshot?.branchName).toBe("main");
    expect(snapshot?.deletedPaths).toEqual([]);
    expect(snapshot?.ignoredPaths).toEqual([]);
  });

  it("reports a detached HEAD as branchName null, not the literal string HEAD", async () => {
    // `git rev-parse --abbrev-ref HEAD` prints the literal "HEAD" when
    // detached. The reader special-cases that string; without the special case
    // callers would build `refs/heads/HEAD`.
    const repo = await createRepo("paperclip-ssh-snapshot-detached-");
    await git(repo, ["checkout", "--detach"]);
    const expectedHead = await git(repo, ["rev-parse", "HEAD"]);

    const snapshot = await readLocalGitWorkspaceSnapshot(repo);

    expect(snapshot).not.toBeNull();
    expect(snapshot?.branchName).toBeNull();
    expect(snapshot?.headCommit).toBe(expectedHead);
  });

  it("lists work-tree-deleted tracked files in deletedPaths", async () => {
    const repo = await createRepo("paperclip-ssh-snapshot-deleted-");
    await writeFile(path.join(repo, "gone.txt"), "gone\n", "utf8");
    await mkdir(path.join(repo, "nested"), { recursive: true });
    await writeFile(path.join(repo, "nested", "also-gone.txt"), "gone\n", "utf8");
    await git(repo, ["add", "gone.txt", "nested/also-gone.txt"]);
    await git(repo, ["commit", "-qm", "add deletable files"]);
    await rm(path.join(repo, "gone.txt"));
    await rm(path.join(repo, "nested", "also-gone.txt"));

    const snapshot = await readLocalGitWorkspaceSnapshot(repo);

    expect(snapshot?.deletedPaths).toEqual(
      expect.arrayContaining(["gone.txt", "nested/also-gone.txt"]),
    );
    // Present files stay out of the list.
    expect(snapshot?.deletedPaths).not.toContain("tracked.txt");
  });

  it("BUG (pinned): .trim() destroys a deleted path's leading and trailing spaces", async () => {
    // `git ls-files --deleted -z` delimits records with NUL, so a leading or
    // trailing space in a record is PART OF THE FILENAME, not padding. The
    // private reader maps `.trim()` over the split output anyway (ssh.ts, in
    // the `deletedPaths` branch), so the emitted path resolves to a file that
    // does not exist and the remote deletion silently misses.
    //
    // >>> THIS ASSERTION PINS A BUG, NOT DESIRED BEHAVIOUR. <<<
    // The convergence onto the shared `readGitWorkspaceSnapshot` is EXPECTED
    // TO FIX IT: the shared reader replaces `.trim()` with a length check
    // (git-workspace-sync.ts, `splitNul`) and keeps every filename byte. When
    // this test goes red after the convergence, that is the fix landing —
    // update the expectations deliberately to the padded name, do not paper
    // over it. `git-workspace-sync.test.ts` already asserts the fixed
    // behaviour for all four of the shared reader's lanes.
    const repo = await createRepo("paperclip-ssh-snapshot-padded-");
    const paddedName = " padded deleted ";
    await writeFile(path.join(repo, paddedName), "padded\n", "utf8");
    await git(repo, ["add", paddedName]);
    await git(repo, ["commit", "-qm", "add padded name"]);
    await rm(path.join(repo, paddedName));

    const snapshot = await readLocalGitWorkspaceSnapshot(repo);

    // Current (buggy) behaviour: padding eaten, inner space preserved.
    expect(snapshot?.deletedPaths).toContain("padded deleted");
    // And the real filename is therefore absent.
    expect(snapshot?.deletedPaths).not.toContain(paddedName);
  });

  it("reads ignored paths, strips the '!! ' prefix, and reports an ignored directory as one entry", async () => {
    const repo = await createRepo("paperclip-ssh-snapshot-ignored-");
    await writeFile(path.join(repo, ".gitignore"), "build/\nartifact.log\n", "utf8");
    await mkdir(path.join(repo, "build", "deep"), { recursive: true });
    await writeFile(path.join(repo, "build", "one.js"), "1\n", "utf8");
    await writeFile(path.join(repo, "build", "deep", "two.js"), "2\n", "utf8");
    await writeFile(path.join(repo, "artifact.log"), "log\n", "utf8");
    await git(repo, ["add", ".gitignore"]);
    await git(repo, ["commit", "-qm", "add gitignore"]);

    const snapshot = await readLocalGitWorkspaceSnapshot(repo);

    expect(snapshot).not.toBeNull();
    // '!! ' prefix stripped and the trailing slash on the directory removed.
    expect(snapshot?.ignoredPaths).toEqual(
      expect.arrayContaining(["build", "artifact.log"]),
    );
    // `--untracked-files=normal` collapses the ignored DIRECTORY to one entry
    // instead of recursing into it. That collapse is the whole point: it keeps
    // the walk cheap on a tree holding a 250 MB `.next`.
    expect(snapshot?.ignoredPaths).not.toContain("build/one.js");
    expect(snapshot?.ignoredPaths).not.toContain("build/deep/two.js");
    expect(snapshot?.ignoredPaths.filter((entry) => entry.startsWith("build"))).toEqual(["build"]);
    // No entry keeps the porcelain prefix.
    for (const entry of snapshot?.ignoredPaths ?? []) {
      expect(entry.startsWith("!! ")).toBe(false);
    }
  });

  it("returns null for a directory that is not a git work tree, without throwing", async () => {
    const rootDir = await mkdtemp(path.join(os.tmpdir(), "paperclip-ssh-snapshot-nongit-"));
    cleanupDirs.push(rootDir);
    const plainDir = path.join(rootDir, "plain");
    await mkdir(plainDir, { recursive: true });
    await writeFile(path.join(plainDir, "file.txt"), "hello\n", "utf8");

    // `git rev-parse --is-inside-work-tree` exits 128 here, so the outer catch
    // is what produces the null; it must not propagate.
    await expect(readLocalGitWorkspaceSnapshot(plainDir)).resolves.toBeNull();
  });

  it("PATCH-0009c: a FAILING ignore walk still yields a snapshot with empty ignoredPaths, not null", async () => {
    // THE MOST IMPORTANT TEST IN THIS FILE.
    //
    // The private reader wraps only the `git status --ignored` call in
    // `.catch(() => ({ stdout: "" }))`, so a failed ignore walk degrades to an
    // empty ignore set while headCommit/branchName/deletedPaths survive. The
    // shared `readGitWorkspaceSnapshot` has NO per-command catch: the same
    // failure rejects the `Promise.all` and collapses the WHOLE snapshot to
    // null. At the `prepareWorkspaceForSshExecution` call site a null snapshot
    // silently downgrades a git-backed run to a flat tar copy — no bundle, no
    // history, and the remote directory is cleared first.
    //
    // Failure is induced with a repo-local config value that only
    // `git status` parses: `status.branch` must be a boolean, so `git status`
    // exits 128 while `rev-parse` and `ls-files --deleted` keep working. That
    // is exactly the shape of the divergence — one command down, the rest fine.
    const repo = await createRepo("paperclip-ssh-snapshot-ignorefail-");
    await writeFile(path.join(repo, ".gitignore"), "build/\n", "utf8");
    await mkdir(path.join(repo, "build"), { recursive: true });
    await writeFile(path.join(repo, "build", "one.js"), "1\n", "utf8");
    await writeFile(path.join(repo, "doomed.txt"), "doomed\n", "utf8");
    await git(repo, ["add", ".gitignore", "doomed.txt"]);
    await git(repo, ["commit", "-qm", "add gitignore and doomed"]);
    await rm(path.join(repo, "doomed.txt"));
    const expectedHead = await git(repo, ["rev-parse", "HEAD"]);

    // Guard: confirm the induced failure really breaks the ignore walk and
    // really leaves the other reads intact, so this test cannot quietly turn
    // into a no-op if a future git changes how it validates config.
    await git(repo, ["config", "status.branch", "notabool"]);
    await expect(
      runLocalGit(repo, ["status", "--ignored", "--porcelain=v1", "-z", "--untracked-files=normal"]),
    ).rejects.toThrow();
    await expect(runLocalGit(repo, ["ls-files", "--deleted", "-z"])).resolves.toBeDefined();

    const snapshot = await readLocalGitWorkspaceSnapshot(repo);

    expect(snapshot).not.toBeNull();
    expect(snapshot?.ignoredPaths).toEqual([]);
    // Everything else still read correctly — the degradation is scoped to the
    // ignore set, which is precisely what the shared reader would NOT do.
    expect(snapshot?.headCommit).toBe(expectedHead);
    expect(snapshot?.branchName).toBe("main");
    expect(snapshot?.deletedPaths).toContain("doomed.txt");
  });
});
