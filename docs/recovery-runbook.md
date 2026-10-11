# Browser login recovery runbook

This runbook separates the MCP transport, the embedded viewer, the Chromium
process, and Google's sign-in decision. A green OAuth page alone does not prove
that the browser is usable.

## Confirmed black-screen failure

The failure captured from the affected deployment followed this sequence:

1. The viewer page loaded and `/auth/browser/status` returned `200`.
2. The viewer requested `/websockify?token=...`.
3. Uvicorn logged `No supported WebSocket library detected` and
   `Unsupported upgrade request`.
4. The application returned `404` for `/websockify`.
5. The page showed a black surface because the RFB connection never received a
   VNC stream.

This is a transport/image problem, separate from Google's “browser or app may
not be secure” response. The image now includes `uvicorn[standard]`, which
provides the WebSocket upgrade implementation. The reverse proxy must still
forward WebSocket upgrades for `/websockify` to the same MCP listener and must
not rewrite the path to a static-file handler.

## Safe checks

Run these checks on the Docker host. They do not read the registry key or
browser cookies:

```bash
docker ps --filter name=gemini-mcp
docker inspect gemini-mcp --format '{{json .State.Health}}'
docker logs --since 10m gemini-mcp | grep -E 'websockify|WebSocket|browser_process|browser_start|healthz|HTTP'
curl -fsS https://gemini.proxmoxserver.dev/healthz
```

The health response should be `{"status":"ok"}`. If it is healthy while the
viewer is black, inspect the browser page's network panel and confirm that the
WebSocket request receives `101 Switching Protocols`, not `404`, `400`, or an
HTML response. The request must remain on the same origin and include its
short-lived viewer capability.

The application now reports the tracked components (`xvfb`, `vnc`, `fluxbox`,
and `chromium`) through the handoff status endpoint. A failed component yields
`browser_process_exited` rather than leaving the user with a misleading
connected black screen. `browser_start_failed` means Chromium could not be
started; inspect the container logs for sandbox, executable, display, or
profile-lock errors.

## Recovery sequence

1. Start a new authorization handoff. Do not reuse an old `sid` or viewer URL.
2. Check `/healthz`, then check that `/websockify` upgrades successfully.
3. If the status reports an exited component, let the handoff expire or cancel
   it and start another. The broker removes its capability and service-owned
   child processes during cancellation, expiry, and shutdown.
4. If a container restart occurred, begin a new handoff. Startup revokes old
   viewer capabilities and recovers only processes carrying the broker's exact
   service markers; it does not terminate unrelated desktop processes.
5. If the browser starts but Google rejects the sign-in, record that as a
   Google compatibility limitation. Do not add stealth flags, user-agent
   spoofing, webdriver removal, or `--no-sandbox`.

The Gemini session supervisor performs its lightweight session check every five
minutes by default. A valid server-side PSID is required before it publishes a
ready state; if the session becomes invalid it records `login_required` and the
next handoff must authenticate again. Reloading a page does not refresh an
expired Google credential.

## Sandbox and Google rejection are different

`GEMINI_CHROMIUM_SANDBOX=true` tests the Chromium sandbox. In an unprivileged
Proxmox LXC it can fail with zygote errors if the host Docker/seccomp/runtime
combination does not permit the required namespaces. Use the pinned profile in
`deploy/chrome-seccomp.json` and verify the host path configured in Compose.

Google's “This browser or app may not be secure” page is an independent sign-in
decision. A working WebSocket and a working sandbox do not guarantee that
Google accepts a Playwright-controlled remote browser. The project does not
claim that it bypasses this protection.

## Rollback without data loss

Keep the existing `gemini-state` volume and `/root/registry.key`. Stop and
remove only the application container, then run the previous known-good image
with the same volume, key, public URL, and proxy configuration. If the sandbox
is the failing change, set `GEMINI_CHROMIUM_SANDBOX=false` for the rollback.
Never run `docker volume rm`, regenerate the registry key, or copy browser
profiles into logs or support tickets.

After rollback, verify `/healthz` and create a fresh handoff. Existing dynamic
client registrations remain usable only when the encrypted state and key are
preserved.
