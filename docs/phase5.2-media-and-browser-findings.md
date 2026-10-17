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
