# Phase 4.5 live protocol recovery

Date: 2026-10-15

## Result

The remaining text-model defect is in the MCP compatibility adapter, not in
authenticated discovery. Discovery supplied distinct, account-scoped route IDs
and model numbers, and the adapter preserved them, but the pinned 1.19 client
did not put `hl=<language>` on the `StreamGenerate` query string. Maintained
Gemini-API binds the language in both that query parameter and `f.req[1]`.
Flash-Lite accepted the incomplete request; the current Flash and Pro routes
returned the web application's generic rejection candidate.

The adapter now binds both values, retains the requested model selector, and
records a credential-free diagnostic summary if the web application still
rejects a candidate. This is an implementation backed by a serializer
comparison and deterministic tests. It is not yet a post-deployment live
success: production was deliberately not rebuilt or restarted.

## Dependency and implementation audit

The MCP directly pins `xob0t/Gemini-API` at
`4402e5231760a1e85e28eb6cd0040ff302b764de`, distributed as
`gemini-webapi==1.19.2.post21`. `pyproject.toml` is the authoritative dependency
declaration. The installed source is not vendored.

The maintained `HanaokaYuzu/Gemini-API` implementation was compared at
`1ad0d4a`. Its `AvailableModel` parser derives the same account capacity and
model number from `GetUserStatus`, builds the same model selector, adds the
selector session and request UUID, fills the 81-field body, and additionally
sets `params["hl"]`. A separate current implementation,
`zhiyu1998/Gemi2Api-Server`, publishes selectors with the same capacity,
model-number, thinking-mode and session tail shape. No evidence supports
inventing low/medium/high values, so independent thinking remains fail-closed.

| Library feature | Installed implementation | MCP implementation | Evidence/result | Required state |
|---|---|---|---|---|
| Model discovery | No dynamic API | Backported authenticated `otAQ7b` parser | Live working | Preserve |
| Flash-Lite routing | Legacy serializer | Account selector + modern body | Live working | Preserve |
| Flash routing | Legacy serializer | Account selector, modern body, now locale-bound | Pre-fix live rejected; mocked fixed | Deploy then verify |
| Pro routing | Legacy serializer | Account selector, modern body, now locale-bound | Pre-fix rejected; bounded recheck timed out; mocked fixed | Deploy then verify |
| Upload | Installed upload helper | Temporary byte bridge | Live working | Preserve |
| Image generation | Old private protocol | Explicit capability gate and parser compatibility | Unsupported for discovered catalog | Do not claim text as image success |
| Thinking levels | No independent control | Evidence-gated fields only | Unsupported | Preserve explicit error |
| Streaming | Older parser, UTF-16 aware | Modern request adapter, no unsafe replay | Text success/rejection classified | Verify after deployment |

`gemini_server_info` now exposes the installed module origin and VCS revision
when `direct_url.json` provides one. Container builds accept `VCS_REF`, publish
it as an OCI image revision label, and expose it as `GEMINI_BUILD_COMMIT`. This
closes the previous `build_commit: unknown` provenance gap for newly built
images without changing persistent volumes or credentials.

## Safe rejection diagnostics

Diagnostics contain only the public requested model, serializer version, model
number, field count, boolean binding checks, HTTP status, and parser stage.
They never include prompts, cookies, tokens, request UUID values, conversation
metadata, route hashes, or response bodies. Requests are not automatically
retried; a failed non-idempotent turn cannot be duplicated by this change.

## Verification

Live observations against the existing deployment before this patch:

- authenticated discovery: Flash-Lite 3.5, Flash 3.6, Pro 3.1;
- Flash-Lite exact-token chat: success (`LIVE45_LITE`);
- Flash exact-token chat: `upstream_rejected`;
- Pro bounded exact-token recheck: connector timeout after approximately 110s;
- server provenance: application `0.2.0`, build commit `unknown`;
- uploads, URL analysis and persistent conversations: retained from the
  independently supplied Phase 4.5 evidence.

Local verification after the patch:

```text
PYTHONPATH=/tmp/gemini-phase41-deps:src python3 -m pytest -q
158 passed in 16.49s
```

The regression suite asserts all three discovered selectors remain distinct,
the model number is mirrored, no Pro-to-Lite fallback occurs, locale binding is
present, diagnostics are sanitized, and build provenance is reported.

## Post-deployment verification matrix

| Capability | Current production | Patched local evidence | Required live follow-up |
|---|---|---|---|
| Flash-Lite | Verified success | 158-test suite pass | Exact token smoke test |
| Flash | Rejected | Locale repair and model identity tests pass | Exact token smoke test |
| Pro | Rejected/timeout | Locale repair and model identity tests pass | Exact token smoke test |
| File uploads | Verified success | Existing regressions pass | One synthetic byte upload |
| Image generation | Explicitly unsupported | Capability gate tests pass | Test only if catalog advertises image support |
| Thinking levels | Explicitly unsupported | Fail-closed tests pass | Capture authenticated evidence first |
| Persistent conversations | Verified success | Existing regressions pass | Two-turn codeword test |
| Browser authentication | Verified existing state | Existing regressions pass | No reset required |
| Model discovery | Verified success | Parser regressions pass | Confirm same account catalog |

No production service, cookie, browser profile, account registry, conversation,
or persistent volume was modified during this work.
