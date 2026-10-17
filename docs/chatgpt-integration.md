# ChatGPT integration

Expose the server only behind HTTPS and configure ChatGPT's MCP connector with
the URL `https://<your-host>/mcp` using Streamable HTTP. The server's public
origin must match `GEMINI_PUBLIC_URL`. Complete the OAuth authorization and
PKCE flow, then separately complete Gemini Web sign-in via browser handoff.
OAuth does not authenticate you to Google.

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
