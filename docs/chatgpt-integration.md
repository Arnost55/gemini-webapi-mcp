# ChatGPT integration

For the hosted service, configure ChatGPT's remote MCP connector with
`https://gemini.proxmoxserver.dev/mcp` using Streamable HTTP. No local install,
server, Docker, or registry-key setup is required. Complete the OAuth
authorization and PKCE flow, then separately complete Gemini Web sign-in via
browser handoff. These first-use authorizations are required; MCP OAuth does
not authenticate you to Google.

The project operator hosts this endpoint on their own server. The account
registry and durable OAuth state are encrypted; review the [security
guide](security.md) for details. The hosted service is intended to be available
most of the time, but has no 100% uptime guarantee and may be interrupted by
crashes, maintenance, network issues, or upstream outages.

To self-host instead, expose the server only behind HTTPS and configure the
connector with `https://<your-host>/mcp`; the server's public origin must match
`GEMINI_PUBLIC_URL`. Hosted availability depends on the operator, while a
self-hosted operator is responsible for service availability, storage, and
security.

Available tools cover account management, model/capability discovery, chat,
conversation history, image generation, file upload, and URL analysis. Ask
ChatGPT to select the intended account before running a request if more than
one is available. Example prompts:

- “List my Gemini accounts and their connection status.”
- “Ask Gemini Flash to explain …”
- “Generate an image of … in a square composition.”
- “Analyze this PDF and summarize the key points.”

Generated images are delivered through native MCP image content and the image
MCP App. Regenerate/edit actions are wired to image generation workflows and
preserve conversation context when available. Download and fullscreen support
depends partly on host bridge support and client sandbox behavior. Desktop and
mobile clients may size or present apps differently; local Chromium tests do
not prove ChatGPT Android acceptance. The user must verify that experience in
the target ChatGPT client. See [media viewer](media-viewer.md).
