# Image and media viewer

The image resource is `ui://gemini-webapi-mcp/image-result-v2.html`; the v1
resource URI remains as a compatibility alias. It uses responsive natural
ratio image sizing, supports one or several images, and includes interactive
gallery, zoom, expanded/fullscreen, save/download, and follow-up actions. The
video resource `ui://gemini-webapi-mcp/video-result-v1.html` is a player/UI
foundation and does not mean video generation is implemented.

The UI reports intrinsic size to compatible hosts and observes layout/media
changes with bounded resizing. Local Chromium regression coverage exercises
15 viewport/aspect-ratio pairs, navigation, fullscreen, zoom reset, download
fidelity, and decode failures. This is not a ChatGPT-host or Android acceptance
test. ChatGPT bridge APIs and sandbox rules can vary; host download support can
fall back or fail, and CSP/embed policies may affect rendering.

If the card is blank or clipped, verify the resolved v2 resource (or v1
compatibility URI), refresh the MCP app resource cache, and capture the actual
client viewport and sanitized console diagnostics. Avoid assuming a CSP warning
alone explains the issue. The repository does not include a genuine authorized
ChatGPT screenshot; add one only from a real approved capture.
