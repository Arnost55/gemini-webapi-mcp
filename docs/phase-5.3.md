# Phase 5.3: Gemini Studio capability report

This release improves image presentation and adds reusable, private video
presentation infrastructure. It does **not** claim an authenticated video
generation or consumer NotebookLM protocol that was not verified.

## Capability matrix

| Area | State | Evidence boundary |
| --- | --- | --- |
| Gemini image generation | Supported | Existing account-scoped Gemini adapter |
| Responsive image gallery | Supported | MCP App v2, with the v1 URI retained for cached descriptors |
| Gemini video in the browser | Observed | Existing authenticated result played as a 1280 x 720, ~10 s video |
| Gemini video generation through MCP | Unsupported | The generation RPC, job identifier, state transitions, expiry, and cancellation protocol were not recovered |
| Private video validation and MCP presentation | Implemented infrastructure | MP4/WebM validation, bounded account-private reads, embedded-resource delivery, and an HTML5 player |
| Video playback in ChatGPT | Unverified | No genuine MCP-generated video job was available for a client end-to-end test |
| Consumer NotebookLM browser access | Observed | The independently authenticated NotebookLM site loaded for the test account |
| Consumer NotebookLM MCP operations | Unsupported | No official consumer API or verified private account-scoped protocol was found |
| NotebookLM Enterprise API | Documented, not configured | Requires a separately licensed Google Cloud project and IAM authorization |

The read-only `gemini_video_capabilities` and `notebooklm_capabilities` tools
return these states to clients without leaking account data.

## Image MCP App

`ui://gemini-webapi-mcp/image-result-v2.html` preserves each artifact's natural
dimensions, uses `object-fit: contain`, and adapts to portrait, landscape, and
square results. It includes a multi-image carousel, image count, keyboard
navigation, download, zoom, and an expanded/fullscreen mode. Regenerate and
edit call the real `gemini_generate_image` tool and preserve the conversation
identifier for edits. The old v1 resource URI serves the corrected renderer so
existing ChatGPT conversations remain compatible.

The app reports bounded intrinsic-height changes after media load, viewport and
orientation changes, selection changes, and expanded-mode changes. A
`ResizeObserver` is disconnected on page exit, and animation-frame coalescing
prevents resize loops. The report measures the app root's bottom edge plus the
document's bottom padding, including any top offset from its first child; this
avoids the small persistent iframe scrollbar caused by measuring root height
alone.

Downloads preserve the validated original PNG/JPEG/WebP bytes. In ChatGPT, the
widget uses the documented `uploadFile` → `getFileDownloadUrl` → `openExternal`
host flow when available. Other clients use a Blob URL and a normal download
anchor; browser sandbox policy may block that fallback, so the viewer tells the
user to use the host's image-save action if no download starts. It does not copy
or display base64. The host flow successfully saved and Pillow-validated a PNG
in a local sandboxed iframe harness. The direct-anchor fallback was also tried
there and the sandbox blocked it, demonstrating why that path is only a
best-effort fallback; the UI does not claim a confirmed save. The production
ChatGPT widget was not changed or deployed for this verification.

## MCP App metadata and hosting

The image and video resources declare empty `connectDomains` and
`resourceDomains`: all media is supplied in the private tool result, so the
widgets need no network access. The legacy ChatGPT CSP keys are emitted beside
the standards-first `_meta.ui.csp` representation.

Set `GEMINI_WIDGET_DOMAIN` only when the widget is hosted on a dedicated HTTPS
origin. Values using HTTP or containing a path are ignored. DNS, TLS, and the
deployment origin must already exist; the server does not provision them.

## Private video presentation

Validated videos can be converted to MCP embedded resources with
`MediaArtifact.as_video_resource(account_id)`. Delivery:

- checks account ownership, completion, and expiry;
- rechecks the stored size before reading;
- enforces `GEMINI_MAX_VIDEO_ARTIFACT_BYTES` and the smaller
  `GEMINI_MAX_EMBEDDED_VIDEO_BYTES` limit (50 MiB by default);
- emits a private `gemini-artifact://` identifier, never a server path or
  public URL;
- supports MP4 and WebM containers in the HTML5 video player.

The player creates a temporary browser object URL, exposes native playback,
seek, volume, and download controls, supports host fullscreen, and revokes the
object URL on exit. This transport is deliberately not connected to a fake
generation tool while the upstream job protocol remains unknown.

## NotebookLM boundary

NotebookLM authentication and protocol state are independent from the Gemini
chat adapter. Gemini cookies, account identifiers, serializers, and model
selectors are not reused. No notebook list/create/upload/chat tools are
registered because doing so without a verified consumer API would create
decorative or misleading operations. Enterprise API support can be added as a
separate adapter after a licensed Google Cloud project, required APIs, and IAM
identity are explicitly configured.

## Deployment

This change does not require destructive storage migration. To deploy after
normal review:

1. Back up and verify the existing `gemini-state` volume and encryption key.
2. Build the image from the committed source.
3. Stop and replace only the application container while retaining the same
   volume and environment.
4. Reconnect or refresh the MCP integration so it discovers the v2 image
   resource and the new read-only capability tools.
5. Verify an existing account and image generation before broader use.

No production deployment was performed as part of Phase 5.3.

## Phase 5.3.2 image viewer recovery

The sandboxed image viewer now uses the MCP image block's validated `data:`
URI directly for previews and gallery thumbnails. The previous Blob URL preview
(`blob:null/...` in an opaque-origin iframe) could be rejected by a host CSP
that permits `img-src data:` but not `blob:`. Download still passes the
original bytes through the host's file-download bridge; it does not depend on
the preview URL. Expanded viewing and gallery navigation share the same
validated source, and broken image decodes surface an explicit error state.

Regression coverage is in `tests/test_image_viewer_browser.py`. It runs a real
Chromium browser in an opaque-origin sandbox with a data-only image CSP, across
mobile and desktop viewports, and checks portrait, landscape, square, gallery,
expanded navigation, download-byte fidelity, and decode failure behavior. Run
it with `uv run --extra browser pytest -q tests/test_image_viewer_browser.py`
after installing the project's browser test extra and configuring a local
Chrome/Edge executable if needed. This verifies the local browser sandbox, not
the ChatGPT Android renderer; client acceptance still requires a live client
test.

## Rollback

Redeploy commit `630da1e2725e0b0a79702269bad9fb888a1a5fc8` with the existing volume
and encryption key unchanged. The release does not delete or transform account
registrations, conversations, artifacts, browser profiles, or keys. Do not
remove the volume as part of rollback.
