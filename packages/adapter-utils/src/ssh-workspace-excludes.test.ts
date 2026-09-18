import { describe, expect, it } from "vitest";

import { sshGitWorkspaceArchiveExcludes, sshGitWorkspaceIgnoredExcludes } from "./ssh.js";

/**
 * Pins the SSH workspace exclusion helpers that PATCH-0008 and PATCH-0009
 * introduced. Both lived only as Python string literals in
 * `docker/patch-0008-ssh-workspace-excludes.py` and
 * `docker/patch-0009-archive-build-output.py` until the overlays were folded
 * into `ssh.ts`, so no test could reach them: the behaviour existed only inside
 * a running container.
 *
 * These are characterization tests. They record what the SSH driver does TODAY,
 * so the V2 convergence onto `git-workspace-sync.ts` has a baseline to diff
 * against rather than a guess. A deliberate behaviour change should update these
 * expectations in the same commit that changes the driver.
 */
describe("sshGitWorkspaceArchiveExcludes", () => {
  it("pins the fixed exclusion floor that every SSH archive carries", () => {
    // A shared project checkout holds Paperclip's sibling worktrees plus
    // ignored package-manager installs. Neither belongs in one run's snapshot;
    // archiving them inflated a 62 MB repo past 2 GB and raced directories
    // other runs were mutating.
    expect(sshGitWorkspaceArchiveExcludes()).toEqual([
      ".git",
      ".paperclip-runtime",
      ".paperclip/worktrees",
      "node_modules",
    ]);
  });
});

describe("sshGitWorkspaceIgnoredExcludes", () => {
  it("emits both a literal entry and a subtree glob for each ignored path", () => {
    // tar needs the directory itself AND its contents; excluding only `dist`
    // still archives `dist/*`.
    expect(sshGitWorkspaceIgnoredExcludes(["dist"])).toEqual(["dist", "dist/*"]);
  });

  it("strips a leading ./ and any trailing slashes from git's output", () => {
    // `git status --ignored --porcelain=v1` reports an ignored DIRECTORY with a
    // trailing slash. Left in place, the tar exclude would not match.
    expect(sshGitWorkspaceIgnoredExcludes(["./build/", "target///"])).toEqual([
      "build",
      "build/*",
      "target",
      "target/*",
    ]);
  });

  it("drops entries that escape the workspace root", () => {
    // Fail closed: a parent-relative or absolute path in the ignore set must
    // never become a tar exclude, or the archive silently loses unrelated
    // files outside the run's workspace.
    expect(sshGitWorkspaceIgnoredExcludes(["../secrets", "/etc/passwd", ""])).toEqual([]);
  });

  it("escapes tar glob metacharacters so a literal path matches literally", () => {
    // A real directory named `cache[1]` must be excluded as that exact name,
    // not interpreted as a character class matching `cache1`.
    expect(sshGitWorkspaceIgnoredExcludes(["cache[1]"])).toEqual([
      "cache\\[1]",
      "cache\\[1]/*",
    ]);
    expect(sshGitWorkspaceIgnoredExcludes(["a*b", "c?d"])).toEqual([
      "a\\*b",
      "a\\*b/*",
      "c\\?d",
      "c\\?d/*",
    ]);
  });

  it("deduplicates paths that normalize to the same entry", () => {
    // `dist`, `dist/`, and `./dist` are one directory; three tar excludes for
    // it is noise that makes the command line grow without bound.
    expect(sshGitWorkspaceIgnoredExcludes(["dist", "dist/", "./dist"])).toEqual([
      "dist",
      "dist/*",
    ]);
  });

  it("returns an empty list for an empty ignore set", () => {
    // The documented degraded mode: PATCH-0009c catches a failed ignore walk
    // to `{ stdout: "" }`, so an empty set means "fall back to the fixed floor",
    // NOT "exclude nothing at all".
    expect(sshGitWorkspaceIgnoredExcludes([])).toEqual([]);
  });
});
