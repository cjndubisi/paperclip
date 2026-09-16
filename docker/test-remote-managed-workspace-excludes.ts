import { describe, expect, it } from "vitest";
import { remoteManagedGitWorkspaceExcludes } from "./remote-managed-runtime.js";

describe("remote managed Git workspace baseline exclusions", () => {
  it("matches the SSH archive exclusions so baseline capture cannot walk sibling worktrees", () => {
    expect(remoteManagedGitWorkspaceExcludes()).toEqual([
      ".git",
      ".paperclip-runtime",
      ".paperclip/worktrees",
      "node_modules",
    ]);
  });
});
