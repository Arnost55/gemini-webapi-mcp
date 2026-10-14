# Phase 4 upstream audit

Audit date: 2026-10-14. Base: `7933e71`. Audited upstream commit:
`xob0t/Gemini-API@4402e5231760a1e85e28eb6cd0040ff302b764de`
(`1.19.2.post21`, 21 commits after tag `v1.19.2`).

## Verified findings

- The project previously depended on the upstream Git `master` branch without a
  commit pin. Docker rebuilds were therefore not reproducible.
- Upstream is maintained at `xob0t/Gemini-API`. The latest stable tag observed
  during the audit is `v1.19.2`; current master reports `1.19.2.post21`.
- Post-release changes include a curl-cffi transport rewrite, resumable uploads,
  Google multi-account URL routing, video support, zombie-stream retry changes,
  and early image-model mismatch detection. These are breaking-risk changes,
  despite the absence of a new stable tag.
- The audited client correctly parses frame lengths as UTF-16 code units and
  uses an incremental UTF-8 decoder for byte chunks.
- The client exposes exactly three static legacy routes. It exposes neither an
  authenticated model enumeration endpoint nor an independent thinking-level
  request parameter.
- No evidence in the audited source verifies routes for the UI labels
  “Gemini 3.5 Flash-Lite”, “Gemini 3.6 Flash”, or “Gemini 3.1 Pro”. No evidence
  verifies low/medium/high encodings. Those mappings remain unavailable unless
  an operator supplies captured, account-authorized route metadata.

## Decision

Use **Option A with a strict compatibility boundary**: pin the already-tested
upstream commit and expose its local compatibility status. This is less complex
than a fork and preserves its upload/image work. A fork or dedicated request
implementation would duplicate a large undocumented protocol without providing
the missing authenticated evidence.

The configured model catalog remains the versioned adapter for verified
captures. It accepts explicit upstream routes and request headers, scopes them
with `ContextVar`, validates thinking levels, and rejects unsupported mappings.
It never infers opaque IDs from UI text.

## Streaming analysis

The older failure mode had two layers: incomplete/truncated streams and cached
clients whose HTTP session had been closed during upstream failure. Phase 3.1
already evicts closed clients. The audited upstream adds incremental UTF-8 and
UTF-16-length-aware framing plus a bounded zombie watchdog. Phase 4 pins that
implementation and adds complete, delayed, truncated, malformed, emoji, and
resume fixtures. Unlimited retry and model substitution remain prohibited.

## Not performed

- No authenticated account/cookies were available in this clean workspace, so
  no live nine-combination, image-byte, upload, URL, or YouTube test is claimed.
- Production Docker, registry, profiles, keys, cookies, conversations, and
  connector configuration were not accessed or changed.
- Consequently, the three production registry entries were not audited here.

## Production acceptance (after approval only)

1. Record the current image digest and retain it as rollback target.
2. Build the PR commit with `GEMINI_BUILD_COMMIT` set to its SHA; do not use an
   unqualified moving dependency.
3. Against a disposable copy of persistent state, call `initialize`, then
   `tools/list`, and confirm the complete list from `gemini_server_info`.
4. Confirm `gemini_server_info.upstream.compatible` is true.
5. Run one request for every model/thinking combination listed as available;
   unsupported combinations must reject rather than downgrade.
6. Run sequential Pro calls, force one interrupted stream, and verify the next
   request creates a healthy client without losing authentication.
7. Validate generated image magic bytes/MIME and upload, URL, YouTube, and
   persistent multimodal conversations.
8. Audit registry entries with the read-only diagnostics tool, comparing
   sanitized profile/account metadata. Do not merge/delete records yet.
9. Refresh/reconnect the ChatGPT MCP connector only after direct `tools/list`
   proves the server list. If ChatGPT still shows nine tools, remove and re-add
   the connector to clear its cached schema.

Rollback: redeploy the recorded prior digest with the same mounts, environment,
secret path, UID, and volume. Never recreate or remove `gemini-state`.
