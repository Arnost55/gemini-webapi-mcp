# Image and media viewer

The current image resource is `ui://gemini-webapi-mcp/image-result-v4.html`; v1,
v2, and v3 resource URIs remain as compatibility aliases. It uses responsive natural
ratio image sizing, supports one or several images, and includes interactive
gallery, zoom, expanded/fullscreen, save/download, and follow-up actions. The
video resource `ui://gemini-webapi-mcp/video-result-v1.html` is a player/UI
foundation and does not mean video generation is implemented.

The UI reports intrinsic size to compatible hosts and observes layout/media
changes with bounded resizing. Local Chromium regression coverage exercises
12 viewport/aspect-ratio pairs, navigation, host-driven fullscreen exit, zoom
reset, download fidelity, and decode failures. This is not a Claude, ChatGPT-host,
or Android acceptance test. Client sandbox rules can vary; host download support
can fall back or fail, and CSP/embed policies may affect rendering.

If the card is blank or clipped, verify the resolved v4 resource (or v1/v2/v3
compatibility URI), refresh the MCP app resource cache, and capture the actual
client viewport and sanitized console diagnostics. Avoid assuming a CSP warning
alone explains the issue. The repository does not include a genuine authorized
ChatGPT screenshot; add one only from a real approved capture.
