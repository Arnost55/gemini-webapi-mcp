# Phase 5.1 browser-grounded protocol evidence

Observed on 2026-10-17 in an authenticated Gemini web session. This record is
intentionally sanitized: it contains no cookies, anti-CSRF values, account
identifiers, full request bodies, or private response payloads.

## Account-visible capability matrix

| UI label | UI description | Thinking choices observed | Chat route status | Image route status |
| --- | --- | --- | --- | --- |
| 3.5 Flash-Lite | Fastest answers | Not independently mapped | Account discovery only | Unverified |
| 3.6 Flash | All-around help | Low, Medium, High shown in UI | Account discovery only | Dedicated image mode verified in browser |
| 3.1 Pro | Advanced reasoning | Low, Medium, High shown in UI | Account discovery only | Unverified |
| 4 Argon | Complex tasks | Low, Medium, High shown in UI | Account discovery only | Unverified |

The labels above are product UI labels, not stable API identifiers. The server
continues to expose only account-scoped routes that its discovery parser and
serializer can verify. It does not guess a route from a display name.

## Network and artifact observations

- The authenticated page used `POST /_/BardChatUi/data/batchexecute` and received
  HTTP 200 responses during the image-generation flow.
- `Create image` opened a dedicated mode with aspect-ratio and image-count
  controls. A simple non-sensitive prompt completed and produced a visible image
  with share, copy, and full-size download controls.
- The rendered image was materialized by the page through a `blob:` URL. This is
  evidence of a successful browser artifact, not proof that every account or MCP
  transport can generate images.
- The UI exposed Low, Medium, and High thinking choices. The capture did not
  establish a stable, independently testable wire-field mapping, so the MCP
  keeps those explicit controls unsupported unless an evidence-fingerprinted
  mapping is configured.

## Parser decision

Hidden reasoning is never concatenated into the public answer. A separate
`reasoning_available` boolean may be recorded by the typed adapter, but reasoning
content is not returned. Trailing follow-up metadata is extracted only when it
is a contiguous block of valid, standalone, self-closing `FollowUp` elements
separated from the answer by a blank line. Inline XML, fenced examples, citations,
and normal text remain untouched.

## Reproduction boundary

This browser pass validates the website behavior for the signed-in account and
the sanitized endpoint shape above. It does not claim a live MCP image call,
Docker runtime sign-in, model-ID mapping, or thinking-field mapping. Those remain
separate acceptance tests.
