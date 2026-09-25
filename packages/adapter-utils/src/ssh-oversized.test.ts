import { describe, expect, it } from "vitest";
import {
  SSH_EXEC_COMMAND_LIMIT_BYTES,
  buildSshRemoteLaunchScript,
  shellQuote,
  sshExecCommandWireBytes,
  sshLaunchRequiresRemoteStaging,
} from "./ssh.js";

// `sprite proxy --ssh` refuses an exec whose PERCENT-ENCODED command exceeds this.
const OBSERVED_REMOTE_REFUSAL_WIRE_BYTES = 65_400;

describe("oversized SSH adapter launches", () => {
  it("keeps the configured threshold below the measured Sprite refusal edge", () => {
    expect(SSH_EXEC_COMMAND_LIMIT_BYTES).toBeLessThan(OBSERVED_REMOTE_REFUSAL_WIRE_BYTES);
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

  it("counts reserved characters at their percent-encoded size", () => {
    expect(sshExecCommandWireBytes("abcXYZ019-._~")).toBe(13);
    expect(sshExecCommandWireBytes("{}' \n")).toBe(15);
    expect(sshExecCommandWireBytes("é")).toBe(6);
  });

  // Regression: REM-243 (2026-09-25). A ~46 KB raw launch full of quotes, JSON
  // and newlines was ~66 KB on the wire, so it went direct and the sprite
  // refused it: "Pi exited with code 255", empty stderr, zero-byte session.
  it("stages a launch that is under the limit raw but over it on the wire", () => {
    const prompt = `## Paperclip Wake Payload\n${'{"issue":"it\'s \\"here\\"","n":1},\n'.repeat(300)}`;
    const script = buildSshRemoteLaunchScript({
      remoteCwd: "/home/sprite/paperclip-workspace",
      envArgs: [`PAPERCLIP_WAKE_PAYLOAD_JSON=${shellQuote(prompt)}`],
      remoteCommandParts: `'pi' '--mode' 'json' '-p' ${shellQuote(prompt)}`,
    });
    const command = `sh -c ${shellQuote(script)}`;
    expect(Buffer.byteLength(command, "utf8")).toBeLessThan(SSH_EXEC_COMMAND_LIMIT_BYTES);
    expect(sshExecCommandWireBytes(command)).toBeGreaterThan(OBSERVED_REMOTE_REFUSAL_WIRE_BYTES);
    expect(sshLaunchRequiresRemoteStaging(script)).toBe(true);
  });
});
