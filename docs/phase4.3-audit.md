# Phase 4.3 regression recovery and Gemini library audit

Audit date: 2026-10-14

## Dependency boundary

The MCP pins `xob0t/Gemini-API` commit
`4402e5231760a1e85e28eb6cd0040ff302b764de`, package
`gemini-webapi==1.19.2.post21`. The dependency is fetched directly from that
Git commit; this repository has no vendored copy or installation-time patch.
Runtime adaptations are isolated in `server._patch_client` and `upstream.py`.

The maintained `HanaokaYuzu/Gemini-API` repository was compared at commit
`1ad0d4a93e505e77c79769d78ef2997641f973c8` (`v2.1.1-2-g1ad0d4a`). That version
is not a drop-in update:

- it requires a different streaming/parser surface (`parse_response_by_frame`
  is no longer exported at the compatibility location used here);
- its exception names and client internals differ from the pinned fork;
- its model API uses dynamically constructed `AvailableModel` objects;
- it supports a verified boolean `extended_thinking` selector, not independent
  low/medium/high levels.

A blind dependency bump therefore breaks the MCP's compatibility checks and
image-response adapter. The pin remains unchanged.

## Backported protocol repair

Maintained upstream obtains account-specific models from the authenticated
`GetUserStatus` (`otAQ7b`) RPC and builds request headers from the returned
model ID, model number, and account-capacity flags. The pinned fork has only
three static model IDs and never calls this RPC.

Phase 4.3 backports that narrow behavior into the MCP adapter:

1. Query `GetUserStatus` through the initialized account client's existing
   authenticated session.
2. Parse only the target RPC response.
3. Derive public model names from account-returned category/display names.
4. Construct the same selector header shape used by maintained upstream.
5. Cache the verified catalog per account.
6. Fall back to the explicitly low-confidence legacy catalog if discovery
   fails or yields no models.

No modern model identifier is stored in source. Independent thinking levels
remain unavailable because neither the authenticated response nor the pinned
client proves low/medium/high mappings. Legacy `gemini-3.0-flash` and
`gemini-3.0-pro` names resolve only to the authenticated model of the same
family; there is no cross-family fallback.

## Root causes and repairs

| Failure | Root cause | Repair |
|---|---|---|
| Flash generic answer | A stale static route can produce a normal-looking error candidate; the exact live phrase was not classified. | Use the authenticated Flash route and reject the observed generic phrase as `upstream_rejected`. |
| Pro stall | The pinned Pro ID is static and may no longer match the account's current Pro route. | Route Pro through the account-derived selector. Existing bounded stall detection remains and never substitutes another family. |
| Bytes upload `AttributeError` | `gemini_upload_file_bytes` called nonexistent `shutil.mkdtemp`. | Call `tempfile.mkdtemp`; retain bounded decoding, extension/MIME validation, and unconditional cleanup. |
| Image rejection | Text-only failure candidates and account/region blocks were conflated. | Classify the library's `ImageGenerationBlocked` as `image_generation_unavailable`; continue to require a genuine image artifact for success. |
| Legacy-only model list | The pinned fork never queried authenticated model capabilities. | Add account-scoped `GetUserStatus` discovery and report its provenance. |
| Thinking level `high` | No verified low/medium/high request mapping exists in either audited implementation. | Continue to fail closed with `thinking_not_supported`. |

## Verification scope

The automated suite covers catalog parsing/routing, unchanged fallback
behavior, unsupported thinking combinations, Flash failure classification,
Pro stall classification without substitution, bytes and path uploads,
extension-based MIME policy, upload cleanup, conversations, account isolation,
authentication state, browser handoff, image response handling, and sensitive
data boundaries.

No production service, account, container, browser profile, cookie store,
conversation, or volume was modified. Live post-change account verification was
not performed from this development checkout because no isolated authorized
test profile was available. Consequently image generation and the exact live
model catalog must be verified after deployment; mocked tests are not claimed
as live success.

## Safe deployment and rollback

Build an immutable image from the merged commit and take a backup/snapshot of
the persistent state before the normal deployment window. Do not replace the
registry encryption key, browser profiles, cookie database, conversation
database, or `gemini-state` volume. After start, verify `gemini_list_models`
reports `authenticated_get_user_status`, then run one non-destructive Flash,
Pro, bytes-upload, and image request against an authorized test account.

Rollback by redeploying the previous immutable application image while keeping
the same persistent volume and encryption key. This change has no schema or
state migration, so application rollback does not require data rollback.
