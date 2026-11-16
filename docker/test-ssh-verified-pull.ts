import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { isDroppedSshPull, parseStagedPullMeta, pullVerifiedFromSsh } from "./ssh.js";

const spec = { host: "h", port: 22, username: "u", privateKey: null, knownHosts: null, strictHostKeyChecking: false } as any;
const payload = Buffer.from("x".repeat(70_000) + "END");
const sha = createHash("sha256").update(payload).digest("hex");
const meta = `PCSTAGED ${payload.length} ${sha}\n`;
const noSleep = async () => {};
const err255 = () => Object.assign(new Error("ssh exited with code 255"), { code: 255 });
const tmpFile = () => path.join(mkdtempSync(path.join(os.tmpdir(), "pv-")), "out.bin");

// fetch writes `bytes` of the payload (simulating a stream cut) or throws a dropped connection.
function fetcher(plan: Array<"ok" | "drop" | number>) {
  const calls: string[] = [];
  return {
    calls,
    fn: async ({ remoteScript, localFile }: any) => {
      calls.push(remoteScript);
      const step = plan.shift() ?? "ok";
      if (step === "drop") { writeFileSync(localFile, ""); throw err255(); }
      writeFileSync(localFile, step === "ok" ? payload : payload.subarray(0, step));
    },
  };
}

describe("PATCH-0015 verified ssh pull", () => {
  it("retries dropped and truncated fetches until size+sha match, then cleans the staged file", async () => {
    const f = fetcher(["drop", 1234, 0, "ok"]);
    const scripts: string[] = [];
    const out = tmpFile();
    const r = await pullVerifiedFromSsh({ spec, producerScript: "tar -cf - .", stagingDir: "/tmp", localFile: out,
      sleep: noSleep, fetch: f.fn, run: async (_s, script) => { scripts.push(script); return { stdout: script.startsWith("rm -f") ? "" : meta }; } });
    expect(r).toEqual({ bytes: payload.length, attempts: 4 });
    expect(readFileSync(out).equals(payload)).toBe(true);
    expect(new Set(f.calls).size).toBe(1);                // every attempt re-reads the SAME staged file
    expect(scripts.at(-1)).toMatch(/^rm -f '\/tmp\/pull-[0-9a-f-]+\.bin'$/);
  });

  it("gives up after the attempt budget with the last reason, leaves no partial local file", async () => {
    const f = fetcher([10, 10, 10]);
    const out = tmpFile();
    await expect(pullVerifiedFromSsh({ spec, producerScript: "x", stagingDir: "/tmp", localFile: out, attempts: 3,
      sleep: noSleep, fetch: f.fn, run: async () => ({ stdout: meta }) })).rejects.toThrow(/after 3 attempts: .*verify failed \(got 10 bytes/);
    expect(() => readFileSync(out)).toThrow();
  });

  it("does not retry a deterministic producer failure (non-255), but does retry a dropped stage", async () => {
    let n = 0;
    const tarFail = Object.assign(new Error("tar: cannot open"), { code: 2 });
    await expect(pullVerifiedFromSsh({ spec, producerScript: "x", stagingDir: "/tmp", localFile: tmpFile(), sleep: noSleep,
      fetch: fetcher([]).fn, run: async (_s, sc) => { if (sc.startsWith("rm")) return { stdout: "" }; n++; throw tarFail; } })).rejects.toThrow(/tar: cannot open/);
    expect(n).toBe(1);
    let m = 0;
    const r = await pullVerifiedFromSsh({ spec, producerScript: "x", stagingDir: "/tmp", localFile: tmpFile(), sleep: noSleep,
      fetch: fetcher(["ok"]).fn, run: async (_s, sc) => { if (sc.startsWith("rm")) return { stdout: "" }; m++; if (m < 3) throw err255(); return { stdout: meta }; } });
    expect(r.attempts).toBe(1);
    expect(m).toBe(3);
  });

  it("rejects malformed/forged stage output and classifies drops strictly", () => {
    for (const bad of ["", "PCSTAGED 12", `PCSTAGED -1 ${sha}`, `PCSTAGED 12 ${sha.slice(1)}`, `PCSTAGED 12 ${sha}; rm -rf /`, "bind: warning"]) {
      expect(() => parseStagedPullMeta(bad)).toThrow();
    }
    expect(parseStagedPullMeta(`bash: warning\n${meta}`)).toEqual({ bytes: payload.length, sha256: sha }); // .bashrc noise before it
    expect(isDroppedSshPull(err255())).toBe(true);
    expect(isDroppedSshPull(Object.assign(new Error("boom"), { code: 1 }))).toBe(false);
    expect(isDroppedSshPull(new Error("tar exited with code 255 because x"))).toBe(false);
  });

  it("quotes the staging path and producer so a hostile dir name cannot inject", async () => {
    const scripts: string[] = [];
    await pullVerifiedFromSsh({ spec, producerScript: "printf hi", stagingDir: "/tmp/a'; rm -rf ~; '", localFile: tmpFile(), sleep: noSleep,
      fetch: fetcher(["ok"]).fn, run: async (_s, sc) => { scripts.push(sc); return { stdout: sc.startsWith("rm") ? "" : meta }; } });
    expect(scripts[0]).toContain(`mkdir -p '/tmp/a'"'"'; rm -rf ~; '"'"''`);
    expect(scripts[0]).not.toMatch(/mkdir -p \/tmp\/a'; rm/);
  });

  it("syncDirectoryFromSsh stages under the run dir so the sprites router can route every pull connection", async () => {
    // Regression (2026-11-16): staging in /tmp carried no /runs/<id>/ marker, the router sent the
    // fetch to the shared probe box and every attempt read 0 bytes.
    const src = (await import("node:fs")).readFileSync(new URL("./ssh.ts", import.meta.url), "utf8");
    const body = src.slice(src.indexOf("export async function syncDirectoryFromSsh"), src.indexOf("// PATCH-0009-gitignore-aware-archive"));
    expect(body).toContain('stagingDir: path.posix.join(input.remoteDir, ".paperclip-runtime")');
    expect(body).not.toContain('stagingDir: "/tmp"');
    const exp = src.slice(src.indexOf("async function exportGitWorkspaceFromSsh"), src.indexOf("async function integrateImportedGitHead"));
    expect(exp).toContain('stagingDir: path.posix.join(input.remoteDir, ".paperclip-runtime")');
  });
});
