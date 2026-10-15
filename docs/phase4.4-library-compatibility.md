# Phase 4.4 custom-library compatibility and protocol recovery

Audit date: 2026-10-15

## Exact dependency relationship

The MCP directly pins `xob0t/Gemini-API` commit
`4402e5231760a1e85e28eb6cd0040ff302b764de`, distributed as
`gemini-webapi==1.19.2.post21`. There is no lockfile, vendored library tree, or
package-install hook. Runtime compatibility patches live in
`gemini_webapi_mcp.server._patch_client`; the dependency declaration resolves
the exact Git revision in `pyproject.toml`.

The installed fork includes its own curl-cffi conversion, resumable upload
work, multi-account endpoints, image parsing, video support, and streaming
watchdog changes. Relevant fork commits immediately before the pin include:

- `795eea4`: resumable, multi-account uploads;
- `e0659f7`: remove the incorrect hardcoded upload `Host` header;
- `4402e52`: detect image-model mismatch during streaming.

The Phase 4.3 bytes-upload `AttributeError` was not a library defect. The MCP
called nonexistent `shutil.mkdtemp`; it now calls `tempfile.mkdtemp`.

## Maintained upstream comparison

Maintained `HanaokaYuzu/Gemini-API` was audited at
`1ad0d4a93e505e77c79769d78ef2997641f973c8` (`v2.1.1-2-g1ad0d4a`). Its
authenticated model work landed after the installed fork diverged:

- `7d9b1de`: dynamic account models and membership-specific headers;
- `2b988bf`: `GetUserStatus`, quotas, modern streaming, model-number request
  fields, and boolean extended thinking;
- `e0deb8a`: route all string selectors through dynamic `AvailableModel`;
- `1ad0d4a`: current audited integration point.

The maintained serializer does not merely copy the model ID into one header.
For every StreamGenerate request it:

1. builds an 81-field body with current browser defaults;
2. mirrors the discovered model number into field 79;
3. writes standard/extended thinking mode into field 80;
4. appends thinking mode and a persistent client-session UUID to the model
   selector header;
5. binds a different per-request UUID in field 59 and
   `x-goog-ext-525005358-jspb`.

The pinned fork builds a 73-field legacy body and performs none of steps 2–5.
Phase 4.3 only backported discovery and the base selector. That explains why
Flash-Lite accepted the request while the stricter Flash and Pro routes
returned an error candidate.

An immediate 2.x dependency upgrade remains unsafe: parser exports,
exceptions, streaming internals, model objects, and image hooks are
incompatible with the MCP's current adapter. Phase 4.4 therefore backports the
verified serializer contract at the existing library boundary. No model ID or
thinking-level value is invented.

## Compatibility matrix

| Library feature | Installed implementation | MCP implementation | Verified behavior | Required fix/state |
|---|---|---|---|---|
| Model discovery | No `GetUserStatus` discovery | Account-scoped RPC backport | Live working | Preserve |
| Flash-Lite routing | Static legacy serializer | Authenticated selector | Live working | Add modern body without changing route identity |
| Flash routing | 73-field body; header only | Authenticated selector header | Live rejected | Mirror model number and bind modern session/request fields |
| Pro routing | 73-field body; header only | Authenticated selector header | Live rejected | Same serializer repair; never substitute another model |
| File upload | Resumable curl-cffi upload | Bounded path/bytes adapter | Live working | Preserve |
| Image generation | Legacy seed model; dict-format parser fallback | Captured image route and native MCP artifact | Live `ValueError` | Modernize request and recover active cookies during `GeneratedImage` construction |
| Thinking levels | No low/medium/high API | Fail-closed validation | Live unsupported | Preserve until authenticated per-level evidence exists |
| Streaming | UTF-16 incremental frame parser and watchdog | Timeout/error classification and client eviction | Flash-Lite/conversations work; modern strict routes rejected before streaming | Preserve parser; repair request initialization first |

## Implemented protocol repair

`_modern_stream_request` applies the maintained upstream request contract to
the pinned client's outgoing StreamGenerate request. It derives model identity
and model number solely from authenticated discovery, uses request-local state,
and preserves the requested route. The same helper modernizes the captured
image route.

The image parser now falls back to the authenticated client's live cookie jar
when the callback receives an empty container. This prevents the precise
Pydantic `ValueError` raised by `GeneratedImage` before an artifact can be
downloaded. A text-only result remains a failure.

Independent low/medium/high thinking is intentionally unchanged. Maintained
upstream proves only a standard/extended boolean (`f.req[80]` values 1/2), and
the authenticated catalog does not label that as low, medium, or high. Mapping
`high` to extended would be speculation.

## Verification and remaining live checks

Deterministic tests assert the complete serialized shapes for Flash-Lite,
Flash, and Pro; exact route preservation; model-number mirroring; UUID binding;
standard thinking mode; image-cookie recovery; useful malformed-image errors;
uploads; conversations; account isolation; and browser handoff.

This development checkout has no isolated authorized Gemini profile. The test
suite therefore proves serialization and parsing behavior, not a live Gemini
response. Staging must confirm exact model identity, a real Flash response, a
real Pro response, and a genuine downloaded image before production rollout.

No production container, profile, cookie, registry, conversation, encryption
key, or persistent volume was accessed or modified.
