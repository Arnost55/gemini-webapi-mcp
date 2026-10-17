# Phase 5.2 media and browser findings

This document records sanitized evidence gathered on 2026-10-17. It contains
no account identifiers, cookies, signed artifact URLs, or generated media.

## MCP presentation contract

The MCP tool result schema has native `ImageContent` (`data` plus `mimeType`),
but no corresponding native video content block. Video therefore requires an
authenticated resource or resource-link delivery contract; a container-local
path is not a client-accessible artifact. MCP defines the wire representation,
not a guarantee that every host renders a particular inline UI.

`gemini_generate_image` now returns validated `ImageContent` blocks directly.
Its structured result contains private artifact identifiers, safe media
metadata, follow-up metadata, and the Gemini conversation identifier. It does
not contain storage paths or base64 text. Actual ChatGPT presentation still
requires testing through a connector running this revision; production was not
deployed during this phase.

## Phase 5.2.2 response-delivery recovery

The installed response stack is MCP Python SDK 1.30.0 (including FastMCP),
Starlette 1.8.0, Uvicorn 0.54.0, HTTPX 0.28.1, Pydantic 2.12.5, and AnyIO
4.15.1. Source inspection and an official MCP client round trip established
that FastMCP preserves `CallToolResult`, `TextContent`, `ImageContent`,
`ResourceLink`, `structuredContent`, and `isError`; no SDK conversion stage
discarded image blocks.

The confirmed server-side compatibility defect was narrower: after Gemini protocol
markup was removed, an otherwise successful image result could contain no
text block at all. That differs from the visible text-tool results and gives a
host UI no compact response to present when it does not render binary blocks.
Every successful image result now begins with a non-empty text block, followed
by the original native image blocks. It also carries structured delivery
metadata and a correlation ID. The original validated private artifact remains
unchanged. This defect is fixed, but the available evidence cannot prove that
it was the sole cause of the observed Android suppression; connector adaptation
and client presentation remain separately unobservable from the server.

An in-process Streamable HTTP diagnostic harness exercised valid SDK types in
nine formats: tiny text, structured metadata, a tiny native image, text plus a
tiny image, text plus a full JPEG, multiple images, metadata plus an image, a
private resource link, and an error result. Each produced a complete JSON-RPC
2.0 response with HTTP 200 and an exact `Content-Length`. The official MCP
client parsed the mixed text/image response as `TextContent` plus
`ImageContent`.

Payload boundary measurements were:

| Source image bytes | Serialized HTTP response bytes | Result |
|---:|---:|---|
| 5,120 | 7,014 | HTTP 200, complete |
| 25,600 | 34,323 | HTTP 200, complete |
| 102,400 | 136,724 | HTTP 200, complete |
| 256,000 | 341,524 | HTTP 200, complete |
| 512,000 | 682,856 | HTTP 200, complete |
| 1,048,576 | 1,398,293 | HTTP 200, complete |
| 2,097,152 | 2,796,393 | HTTP 200, complete |

These measurements establish no server, FastMCP, ASGI, or direct-client
truncation through 2 MiB of source media. They do **not** establish a ChatGPT
connector or Android UI payload limit.

The image tool also advertises a private `ui://` MCP App resource using both
the MCP Apps `ui.resourceUri` metadata and OpenAI's compatibility
`openai/outputTemplate` metadata. The static viewer renders image blocks from
the tool-result envelope as in-memory data URLs; it makes no external request
and creates no unauthenticated artifact URL. Native image blocks and the text
fallback remain available to clients that do not support MCP Apps.

When `GEMINI_IMAGE_DELIVERY_DIAGNOSTICS=1`, metadata-only events record the
generation, download, validation, serialization-ready, and ASGI transmission
stages. They include correlation ID, safe MIME/count/size data, duration,
status, and sanitized exception category. Prompts, cookies, paths, image bytes,
and base64 are not accepted by the logger's allowlist. ASGI send completion is
reported as server transport completion only, never as connector
acknowledgement or user-interface display.

OpenAI's current connector guidance describes MCP tool results as model input
and documents MCP Apps as the supported route for a custom inline interface.
It does not promise that a custom MCP connector automatically renders an
arbitrary `ImageContent` tool-result block as a native conversation image.
Therefore a valid image block is a server/protocol guarantee, not proof of
ChatGPT's presentation behavior. If raw image blocks are not surfaced, the MCP
App resource associated with the tool can render an authorized result inline.
That would be an inline app surface; it does not guarantee the exact native
attachment presentation of ChatGPT's built-in image generator.

## Authenticated Gemini browser observations

- Flash-Lite, Flash, and Pro were visible model choices.
- A Pro request returned the requested exact token twice. The deployed MCP's
  Pro rejection is therefore not explained by account entitlement. Phase 5.1
  did not semantically change the extracted serializer, so the remaining
  failure is classified as drift in the private model route/request or its
  candidate response, not a safe basis for a guessed field change.
- Low, Medium, and High thinking choices were visible for Pro. The generation
  request itself was not observable in the captured page target, so no stable,
  sanitized field mapping was established. The MCP deliberately retains
  `thinking_not_supported`.
- Video generation was available. A requested 16:9 test completed and rendered
  as a playable 1280 by 720 video with a measured duration of 10.005 seconds.
  Play, mute, share, and download controls were visible. An automated download
  event did not complete, so downloadability and container MIME were not
  independently verified.

## Video implementation boundary

The common artifact layer validates MP4/WebM signatures, enforces account
ownership, supports expiry and bounded streaming, and keeps storage private.
No `gemini_generate_video` tool was added because neither a stable generation
wire mapping nor an authenticated client-accessible artifact delivery endpoint
was recovered. Returning a local path, an unverified signed URL, or a fabricated
job contract would violate the requested security and truthfulness constraints.
