# Universal browser-login acceptance

This procedure validates the custom embedded viewer at the deployment's HTTPS
origin. It does not expose port 6080 or the standard noVNC desktop page.

## Before testing

Set `GEMINI_PUBLIC_URL`, `GEMINI_NOVNC_WS_URL`, and a deliberate
`GEMINI_VIEWER_FRAME_ANCESTORS` value. Confirm that the reverse proxy forwards
WebSocket upgrades for `/websockify`, sends `Referrer-Policy: no-referrer`, and
does not log query strings. Use an authorized test Google account only.

## Desktop

1. Start a fresh OAuth authorization from ChatGPT or another MCP client.
2. Open the generated `/auth/browser/viewer?sid=...` URL.
3. In Chrome and Edge on Windows, verify that the page shows only the browser
   viewport and compact controls, with no Linux desktop or noVNC toolbar.
4. Verify mouse movement, left/right click, wheel scrolling, text selection,
   keyboard focus, fullscreen, resize, reconnect messaging, and the countdown.
5. Sign in to Gemini, return to the OAuth page, and complete authorization.
6. Verify the viewer closes or becomes unusable after completion and a second
   handoff cannot reuse the first capability.

## Mobile and tablet

Run the same flow in Android Chrome, Samsung Internet, iOS Safari, and a tablet
browser in portrait and landscape. Verify single tap, swipe scrolling, long
press, pinch/viewport resizing, keyboard input, Backspace, Enter, Tab, and text
selection. A touch-only device must not show a remote or CSS cursor. A hybrid
device must support the mode switch without restarting the handoff.

## Security checks

- Expired or cancelled `sid` values return an unusable page/status.
- An unlisted `Origin` cannot cancel a handoff or embed the viewer.
- The viewer response contains no Google URL, cookie, password, profile path,
  screenshot, or clipboard content.
- Two simultaneous handoffs use separate browser profiles and cannot send input
  to each other's RFB target.
- Restart cleanup removes abandoned service-owned profiles and capabilities,
  while leaving unrelated browser processes untouched.

Physical-device and live-Google results must be recorded with the device,
browser version, date, and authorized test account class. Automated unit tests
do not substitute for this procedure. Browser automation is optional in this
repository because the Playwright extra is not installed by default.
