#!/usr/bin/env bash
# ADR-0021 GO-2 spike — can `agentosd mcp` distinguish multiple agent callers multiplexed over ONE
# MCP stdio connection (the installed-Hermes topology)? Drive the real perceive server with two
# `tools/call` frames on one stdin stream and inspect what discriminator each frame carries.
#
# Run from repo root after `cargo build -p agentosd`:  bash spikes/mcp-session-granularity/probe.sh
set -euo pipefail
BIN="${AGENTOSD_BIN:-target/debug/agentosd}"
[ -x "$BIN" ] || { echo "build first: cargo build -p agentosd"; exit 1; }

echo "# two callers, one stdio connection — server responses:"
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"gpu_status"}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"gpu_status"}}' \
  | timeout 5 "$BIN" mcp 2>/dev/null

cat <<'NOTE'

# FINDING: the only per-frame discriminator is the JSON-RPC `id` (2, 3). There is NO caller / session
# / principal field — MCP stdio is one bidirectional stream = one session per `initialize`, and the
# server (crates/agentosd/src/mcp.rs) reads only `id` + `method` + `params`. So when Hermes routes
# parent + N in-process thread-children through its single shared ClientSession
# (mcp_tool.py: module-level `_servers`, one MCPServerTask.session, one initialize; tool calls via
# `server.session.call_tool`; delegate_tool.py: children INHERIT the toolset, no new session), the
# server cannot tell which child sent a given call. Both isolation layers are therefore blind on the
# Hermes path: the daemon sees one bus name (layer-1), and the MCP server sees one session (layer-2).
# => GO-2 for the Hermes act path needs an UPSTREAM Hermes change (per-child MCP connection, or a
#    trusted per-child principal injected out-of-band). Until then: Claude-Code stdio scope only
#    (subprocess-per-session => distinct bus name => layer-1 suffices), Hermes path GATED.
NOTE
