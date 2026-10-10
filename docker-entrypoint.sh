#!/bin/sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
export GEMINI_DISPLAY_WIDTH="${GEMINI_DISPLAY_WIDTH:-1440}"
export GEMINI_DISPLAY_HEIGHT="${GEMINI_DISPLAY_HEIGHT:-900}"

Xvfb "$DISPLAY" -screen 0 "${GEMINI_DISPLAY_WIDTH}x${GEMINI_DISPLAY_HEIGHT}x24" -nolisten tcp >/tmp/xvfb.log 2>&1 &
fluxbox >/tmp/fluxbox.log 2>&1 &

# VNC/noVNC are intentionally loopback-only inside the container. Put them
# behind the one-time authenticated handoff route in the reverse proxy.
x11vnc -display "$DISPLAY" -localhost -forever -shared -nopw -rfbport "${GEMINI_VNC_PORT:-5900}" >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ "127.0.0.1:${GEMINI_NOVNC_PORT:-6080}" "127.0.0.1:${GEMINI_VNC_PORT:-5900}" >/tmp/novnc.log 2>&1 &

exec uv run gemini-webapi-mcp --transport streamable-http --host 0.0.0.0 --port 8000
