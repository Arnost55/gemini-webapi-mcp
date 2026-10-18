# Client compatibility

`gemini_generate_image` uses a standards-first result on every successful
request: a non-empty MCP `TextContent` block followed by one or more native MCP
`ImageContent` blocks. `structuredContent` contains optional artifact metadata,
and `_meta.ui.resourceUri` associates the optional MCP App gallery. Neither
optional field is required to recover the image bytes.

MCP Apps support is negotiated by the host as an extension. The server keeps
the image tool useful when that extension is absent: clients may ignore the UI
metadata, `structuredContent`, or both and still consume the standard `content`
array. The server does not branch on client brand or user agent. A resource
link is not treated as proof of inline image rendering.

## Client compatibility matrix

These labels deliberately separate protocol validation from host UI behavior.
"Native image block received" means a direct standards-compliant MCP client
decoded the block; it does not mean a named product rendered it in a native
assistant message.

| Client / harness | Connection works | Text chat works | Image generation works | Native image block received | Image displayed | Embedded viewer supported | Download works | Known limitations |
|---|---|---|---|---|---|---|---|---|
| Official Python MCP SDK over Streamable HTTP | Verified | Not exercised in this harness | Simulated tool result verified | Verified | Unsupported (no product UI) | Untested | Untested | Protocol/transport test only; no authenticated Gemini generation |
| MCP Apps-capable local Chromium harness | Verified | Not exercised | Simulated result verified | Verified by fixture | Verified in local viewer | Verified | Verified | Local browser harness, not a third-party host acceptance test |
| MCP Apps-unaware/content-only test host | Verified | Not exercised | Simulated result verified | Verified | Untested | Unsupported | Untested | Intentionally ignores `structuredContent` and `_meta` |
| ChatGPT Web | Partially verified | Previously observed through the connector | Partially verified | Server/transport verified; host receipt not independently observable | Untested on this revision | Partially verified | Untested on this revision | Client presentation and cache behavior are host-controlled |
| ChatGPT Android | Untested | Untested on this revision | Untested | Untested | Untested | Untested | Untested | Requires a real authorized device acceptance run |
| Claude | Untested | Untested | Untested | Untested | Untested | Untested | Untested | A custom MCP connection does not by itself prove interactive-connector eligibility or chat-body placement |
| Cursor | Untested | Untested | Untested | Untested | Untested | Untested | Untested | No authenticated acceptance environment was available |
| VS Code | Untested | Untested | Untested | Untested | Untested | Untested | Untested | No authenticated acceptance environment was available |

## What the server can guarantee

- Core MCP output uses SDK `CallToolResult`, `TextContent`, and `ImageContent`
  types and places image blocks in the real `content` array.
- JPEG, PNG, and WebP artifacts are decoded and validated before delivery;
  original validated bytes and MIME types are preserved.
- Single and multiple images, including portrait, landscape, and square
  dimensions, use the same response path.
- Gemini image placeholders are removed only when a corresponding image was
  successfully extracted. Recognized trailing follow-up suggestions become
  structured metadata; ordinary XML, HTML, Markdown, citations, and fenced code
  remain untouched.
- The private MCP App consumes the already-authorized in-memory tool result. It
  does not create a public artifact endpoint.

## What remains client-controlled

MCP defines the tool-result payload, but each client decides whether and where
to render native image blocks, whether to load MCP Apps, whether to expose the
host download bridge, and whether a tool result appears in a preview or in the
assistant's native message. Therefore protocol validity, client ingestion,
inline display, and assistant-message placement are four separate acceptance
criteria.

The server advertises the stable MCP Apps resource metadata while always
returning the standard fallback. The installed Python SDK accepts extension
capabilities but does not provide per-session dynamic tool registration in
FastMCP; static optional metadata is safe for unaware clients to ignore. No
unverified capability flag or client-name heuristic is used.
