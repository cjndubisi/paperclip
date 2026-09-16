import { describe, expect, it } from "vitest";
import { sshGitWorkspaceArchiveExcludes } from "./ssh.js";

describe("SSH git workspace archive exclusions", () => {
  it("excludes Paperclip sibling worktrees and disposable dependency trees", () => {
    expect(sshGitWorkspaceArchiveExcludes()).toEqual([
      ".git",
      ".paperclip-runtime",
      ".paperclip/worktrees",
      "node_modules",
    ]);
  });
});
