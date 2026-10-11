#!/bin/sh
set -eu

# noVNC listens on the container interface so Docker can publish its web port.
# Each target VNC server stays on loopback and is created only for one login.
# websockify resolves each opaque token to a distinct loopback VNC server.
# The application updates this token map atomically when it creates/consumes a
# login handoff. There is deliberately no shared Chromium desktop.
handoff_root="${GEMINI_HANDOFF_ROOT:-/var/lib/gemini-mcp/handoffs}"
mkdir -p "$handoff_root"
touch "$handoff_root/novnc-tokens"
websockify --web=/usr/share/novnc/ --token-plugin TokenFile --token-source "$handoff_root/novnc-tokens" "0.0.0.0:${GEMINI_NOVNC_PORT:-6080}" >/tmp/novnc.log 2>&1 &
websockify_pid=$!
cleanup() {
  kill "$websockify_pid" 2>/dev/null || true
  wait "$websockify_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

uv run gemini-webapi-mcp --transport streamable-http --host 0.0.0.0 --port 8000
status=$?
exit "$status"
