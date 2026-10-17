# Image generation and editing

Use `gemini_generate_image` with a prompt and, when editing, an existing
conversation or supported source image. The tool returns generated image
content through native MCP image blocks and private artifact metadata; storage
paths are not intended as client URLs. Multiple outputs are presented in the
image gallery when provided by the upstream response.

The MCP App supports natural aspect-ratio display, gallery selection, zoom,
fullscreen/expanded viewing, download, and follow-up regenerate/edit actions.
Some actions depend on client host APIs. Do not treat a visual control as proof
that the host supports it; failures should be surfaced as actionable UI errors.
See [media viewer](media-viewer.md) for host limitations and diagnostics.

Image generation depends on an authenticated Gemini Web account and upstream
protocol availability. This project does not promise a particular account
entitlement, output count, or resolution. Respect upstream limits and avoid
uploading content you are not authorized to process.
