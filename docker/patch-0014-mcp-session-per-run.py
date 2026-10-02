"""PATCH-0014 — reuse one remote MCP session per (connection, agent run).

Root cause: server/dist/services/tool-gateway.js executeRemoteHttpTool() runs a
fresh `initialize` handshake before EVERY tools/call when the connection has
`mcpSessionRequired: true`. Stateful MCP servers keep per-session state that the
next call needs; Executor self-host keeps a paused (approval-gated) execution
only in the engine of the session that created it. So `execute` pauses in
session A, `resume` lands in brand-new session B and always gets
"Paused execution is unknown" (UsefulSoftwareCo/executor#1778). Observed live on
REM-421: resume 1 s after the pause still missed.

Fix: cache the Mcp-Session-Id (+ negotiated MCP-Protocol-Version) the server
returned, keyed by connection id + heartbeat run id (falling back to the gateway
session id when there is no run). Later calls in the same run reuse it.
 - Only cached when the server actually returned an Mcp-Session-Id, so
   stateless servers are untouched.
 - Scoped to one run: nothing is shared across agents or runs.
 - Credentials are NOT cached: fresh headers are built per call and only the two
   session headers are layered on top.
 - HTTP 404 on a cached session (server evicted/restarted it, MCP spec
   "session terminated") drops the cache entry and returns the error as today.
   No retry: a forgotten session is forgotten, and re-sending a write is unsafe.
 - Entries idle for 30 min (Executor's own session idle TTL) are pruned lazily.

Idempotent: re-running prints ALREADY-CURRENT.
"""
import sys, pathlib

F = "/app/server/dist/services/tool-gateway.js"
MARKER = "PATCH-0014"

p = pathlib.Path(F)
if not p.exists():
    print("PATCH-0014 TARGET-MISSING %s" % F)
    sys.exit(1)

src = p.read_text()
if MARKER in src:
    print("PATCH-0014 ALREADY-CURRENT")
    sys.exit(0)

edits = [
    # 1. module-level cache (shared by every createToolGatewayService instance in the process)
    (
        "export function createToolGatewayService(db, options = {}) {",
        "// PATCH-0014: one remote MCP session per (connection, agent run).\n"
        "const PATCH_0014_MCP_SESSION_IDLE_MS = 30 * 60 * 1000;\n"
        "const patch0014McpSessions = new Map();\n"
        "function patch0014McpSessionKey(connection, session) {\n"
        "    return `${connection.id}:${session.runId ?? session.id}`;\n"
        "}\n"
        "function patch0014GetMcpSession(key) {\n"
        "    const now = Date.now();\n"
        "    for (const [k, v] of patch0014McpSessions) {\n"
        "        if (now - v.lastUsed > PATCH_0014_MCP_SESSION_IDLE_MS)\n"
        "            patch0014McpSessions.delete(k);\n"
        "    }\n"
        "    return patch0014McpSessions.get(key) ?? null;\n"
        "}\n"
        "export function createToolGatewayService(db, options = {}) {",
    ),
    # 2. reuse the cached session instead of re-initializing on every call
    (
        "            let requestHeaders = headers;\n"
        "            if (connection.config.mcpSessionRequired === true) {\n"
        "                requestHeaders = await initializeMcpHttpSession({\n"
        "                    send: (init) => dispatchRemote(endpoint, {\n"
        "                        ...init,\n"
        "                        redirect: \"manual\",\n"
        "                        signal: controller.signal,\n"
        "                    }),\n"
        "                    headers,\n"
        "                    requestId,\n"
        "                });\n"
        "            }\n",
        "            let requestHeaders = headers;\n"
        "            // PATCH-0014: reuse this run's MCP session; initialize only when there is none.\n"
        "            const patch0014Key = patch0014McpSessionKey(connection, session);\n"
        "            let patch0014Cached = null;\n"
        "            if (connection.config.mcpSessionRequired === true) {\n"
        "                patch0014Cached = patch0014GetMcpSession(patch0014Key);\n"
        "                if (patch0014Cached) {\n"
        "                    requestHeaders = { ...headers, ...patch0014Cached.sessionHeaders };\n"
        "                    patch0014Cached.lastUsed = Date.now();\n"
        "                }\n"
        "                else {\n"
        "                    requestHeaders = await initializeMcpHttpSession({\n"
        "                        send: (init) => dispatchRemote(endpoint, {\n"
        "                            ...init,\n"
        "                            redirect: \"manual\",\n"
        "                            signal: controller.signal,\n"
        "                        }),\n"
        "                        headers,\n"
        "                        requestId,\n"
        "                    });\n"
        "                    const sid = requestHeaders[\"Mcp-Session-Id\"];\n"
        "                    if (sid) {\n"
        "                        patch0014McpSessions.set(patch0014Key, {\n"
        "                            sessionHeaders: {\n"
        "                                \"Mcp-Session-Id\": sid,\n"
        "                                \"MCP-Protocol-Version\": requestHeaders[\"MCP-Protocol-Version\"],\n"
        "                            },\n"
        "                            lastUsed: Date.now(),\n"
        "                        });\n"
        "                    }\n"
        "                }\n"
        "            }\n",
    ),
    # 3. server forgot the session -> drop it, no retry
    (
        "            let response = await dispatchRemote(endpoint, requestInit);\n",
        "            let response = await dispatchRemote(endpoint, requestInit);\n"
        "            // PATCH-0014: 404 on a reused session = server terminated it. Forget it; no retry.\n"
        "            if (response.status === 404 && patch0014Cached) {\n"
        "                patch0014McpSessions.delete(patch0014Key);\n"
        "            }\n",
    ),
]

for anchor, _ in edits:
    n = src.count(anchor)
    if n != 1:
        print("PATCH-0014 ANCHOR-FAIL count=%d anchor=%r" % (n, anchor[:70]))
        sys.exit(1)

for anchor, replacement in edits:
    src = src.replace(anchor, replacement)

p.write_text(src)
print("PATCH-0014 APPLIED")
