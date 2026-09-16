import { describe, expect, it } from "vitest";
import {
  SSH_EXEC_COMMAND_LIMIT_BYTES,
  buildSshRemoteLaunchScript,
  sshLaunchRequiresRemoteStaging,
} from "./ssh.js";

const OBSERVED_REMOTE_REFUSAL_BYTES = 65_400;

describe("oversized SSH adapter launches", () => {
  it("keeps the configured threshold below the measured Sprite refusal edge", () => {
    expect(SSH_EXEC_COMMAND_LIMIT_BYTES).toBeLessThan(OBSERVED_REMOTE_REFUSAL_BYTES);
  });

  it("stages launches over the threshold", () => {
    const script = buildSshRemoteLaunchScript({
      remoteCwd: "/home/sprite/paperclip-workspace",
      envArgs: ["PAPERCLIP_RUN_ID='run-test'"],
      remoteCommandParts: `'pi' '${"x".repeat(86_000)}'`,
    });
    expect(sshLaunchRequiresRemoteStaging(script)).toBe(true);
  });

  it("keeps small launches on the direct path", () => {
    const script = buildSshRemoteLaunchScript({
      remoteCwd: "/home/sprite/paperclip-workspace",
      envArgs: [],
      remoteCommandParts: "'pi' '--help'",
    });
    expect(sshLaunchRequiresRemoteStaging(script)).toBe(false);
  });
});
