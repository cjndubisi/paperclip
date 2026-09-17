"""PATCH-REM199 — hermes_gateway final-message cap.

Root cause: packages/adapters/hermes/src/gateway/server/execute.ts mapped the
Hermes run's final output to `summary: output.slice(0, 2_000)`. Paperclip posts
that `summary` to the issue thread as the agent's comment whenever the run has
no direct-API write path (authorizationReason = internal_agent_write), so any
agent ruling longer than 2000 chars was severed mid-sentence with no marker.
The full text was never lost upstream: heartbeat_runs.result_json->>'output'
retains it.

Fix: raise the bound to 64k and make any residual truncation VISIBLE, so a cut
decision can never be mistaken for a complete one.

Idempotent: re-running prints ALREADY-CURRENT.
"""
import sys, pathlib

F = "/app/packages/adapters/hermes/src/gateway/server/execute.ts"
MARKER = "PATCH-REM199"

p = pathlib.Path(F)
if not p.exists():
    print("PATCH-REM199 TARGET-MISSING %s" % F)
    sys.exit(1)

src = p.read_text()
if MARKER in src:
    print("PATCH-REM199 ALREADY-CURRENT")
    sys.exit(0)

anchor = "    ...(output ? { summary: output.slice(0, 2_000) } : {}),"
if src.count(anchor) != 1:
    print("PATCH-REM199 ANCHOR-FAIL count=%d" % src.count(anchor))
    sys.exit(1)

replacement = (
    "    // PATCH-REM199: the 2_000-char cap silently severed agent rulings mid-sentence.\n"
    "    // Raise the bound well above any observed comment and make any residual\n"
    "    // truncation VISIBLE, so a cut decision can never read as a complete one.\n"
    "    ...(output\n"
    "      ? {\n"
    "          summary:\n"
    "            output.length <= 64_000\n"
    "              ? output\n"
    "              : `${output.slice(0, 64_000)}\\n\\n[truncated ${output.length - 64_000} chars \\u2014 full text in run resultJson.output]`,\n"
    "        }\n"
    "      : {}),"
)

p.write_text(src.replace(anchor, replacement))
print("PATCH-REM199 APPLIED")
