# Phase 4.1 authenticated protocol recovery

## Status

Phase 4.0 is merged in `main` at `8240e9e`. The live ChatGPT connector queried
on 2026-10-14 still exposed its nine-tool schema and returned only the three
legacy fallback models. It did not expose `gemini_server_info`, while a clean
local Streamable HTTP client sees 20 tools. This distinguishes a stale deployed
image or connector schema from local tool registration; production digest/build
SHA must be checked before reconnecting the connector.

The authorized cloud browser had no signed-in Gemini session. The live MCP
session could execute requests but exposed no request-capture diagnostic.
Therefore no user-account modern route or thinking-level mapping is claimed.

### Follow-up upstream audit (2026-10-14)

The maintained `HanaokaYuzu/Gemini-API` branch was inspected at commit
`1ad0d4a93e505e77c79769d78ef2997641f973c8` (package `2.1.1.post2`). It now
parses account-scoped models from the authenticated `GetUserStatus` RPC and
builds `x-goog-ext-525001261-jspb` headers from the returned model id, capacity,
and model number. This proves that authenticated discovery is implemented
upstream, but not which routes the user's account receives.

That commit is not a safe drop-in upgrade. A clean compatibility run fails
during test collection because `parse_response_by_frame` and
`RequestTimeoutError` were removed or replaced, and the MCP image/download
adapter relies on other old client internals. The maintained branch also still
serializes only an `extended_thinking` boolean; it does not expose independently
verified low/medium/high web controls. The dependency pin therefore remains
unchanged pending a complete migration and authenticated regression run.

## Public-source observations, not accepted mappings

Two maintained independent Gemini-web implementations now use private request
slots 79/80 and model header `x-goog-ext-525001261-jspb`. Their model labels,
hashes, defaults, and even reported effective backend models disagree. One
project documents a case where a requested Pro selector returned Flash. This is
useful implementation evidence for an adapter, but not proof of the user's
account routing or low/medium/high behavior.

Accordingly, Phase 4.1 does **not** bake those third-party constants into the
default catalog.

## Implemented adapter

Versioned configured catalogs may now carry:

- a model header;
- allow-listed model selector fields 17, 31, 79, 80, 91, and 96;
- distinct header/field mappings for low, medium, and high;
- a protocol version and SHA-256 evidence fingerprint.

The selection is applied through request-local `ContextVar` state immediately
before StreamGenerate serialization. Concurrent calls cannot share it. A route
with invalid or missing selector evidence is rejected. Prompt, file, UUID,
conversation, auth, and token slots cannot be configured through this adapter.

The adapter recomputes the SHA-256 over normalized per-level selector metadata
and fails the route closed if it differs from the supplied fingerprint. Only
the model-routing header is accepted, preventing a catalog from injecting
cookies, authorization, or arbitrary HTTP headers. Duplicate level captures
and oversized or invalid model headers are rejected by the sanitizer.

`gemini-protocol-capture` sanitizes an authorized local capture into this
catalog format. Its regression fixture proves that prompts, conversation IDs,
request UUIDs, cookies, and unrelated fields do not survive.

## Minimum evidence collection

This procedure must be performed in the user's own authenticated browser. It
does not require sharing a HAR or cookies.

1. In Gemini, select one visible model and `Low` thinking.
2. Open DevTools Network, clear requests, send the harmless prompt
   `Return exactly: protocol-check-low`.
3. Select the `StreamGenerate` request and locally copy only:
   - the value of `x-goog-ext-525001261-jspb`;
   - the form value `f.req`.
4. Repeat with `Medium` and `High`, using corresponding harmless tokens.
5. Put those values into a local JSON file shaped as:

```json
{
  "model_id": "chosen-public-id",
  "label": "Exact visible UI label",
  "captures": [
    {"thinking_level": "low", "model_header": "[...]", "f_req": "[...]"},
    {"thinking_level": "medium", "model_header": "[...]", "f_req": "[...]"},
    {"thinking_level": "high", "model_header": "[...]", "f_req": "[...]"}
  ]
}
```

6. Run `gemini-protocol-capture input.json sanitized.json` locally. Inspect the
   output: it must contain only the model header and allow-listed numeric slots.
7. Repeat for each visible model. Compare captures across a fresh browser
   session to detect rotating values.
8. Load only `sanitized.json` as `GEMINI_MODEL_CATALOG_JSON` in a disposable
   test environment and run the nine-combination behavioral matrix. A response
   is not proof of thinking selection: response metadata/markers and latency or
   token differences must agree across repeated controlled prompts.

Never share cookies, `Authorization`, `at`, `f.sid`, full HAR files, or response
conversation content.

## Pro failures

`upstream_stall` is a progress/watchdog failure and requires client eviction.
`upstream_rejected` means a response/rejection was received. They remain
separate error classes. Current evidence suggests the legacy Pro route may be
stale or rejected for the account, but does not prove whether entitlement,
selector metadata, or another request field is responsible. Model substitution
and unbounded retries remain prohibited.

## Verification performed

- Full automated suite: 130 passing after selector-evidence hardening.
- Maintained-upstream compatibility probe: failed safely at collection on three
  removed or renamed client surfaces; the production pin was not changed.
- Existing real Streamable HTTP initialize/tools/list test: passing.
- Direct local registration: 20 tools (Phase 4.0 evidence, unchanged).
- Live connector: nine exposed tools, three ready opaque account records, three
  legacy fallback models, no modern capability endpoint.
- Authenticated modern model/thinking matrix: not performed; precise blocker is
  absence of sanitized selector captures from a signed-in UI session.
- Multimodal live tests: not performed in this phase; no production mutation or
  test-account authorization was available.

## Deployment and rollback

Do not deploy until sanitized captures and the live matrix are reviewed. Then:

1. record the running image digest and `gemini_server_info` build SHA;
2. build the reviewed commit and test it against a disposable copy of state;
3. verify direct `tools/list` and the complete model matrix before refreshing
   the ChatGPT connector;
4. preserve every existing mount, secret, UID, profile, and conversation DB;
5. rollback by redeploying the recorded digest with unchanged storage.

Never remove or recreate `gemini-state` during rollback.
