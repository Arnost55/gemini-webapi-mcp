# ADR-005: Isolate Gemini Web behind a typed, versioned adapter

Status: accepted for Phase 5.0 (2026-10-15)

## Decision

Keep Python, FastMCP, the encrypted registry, SQLite conversations, and the
Chrome/Xvfb/noVNC login handoff. Introduce an application-owned `protocol`
package containing typed request/response/model contracts, a stable error
taxonomy, an account-scoped model registry, pure request serialization, stream
state tracking, and a replaceable adapter interface.

The pinned `xob0t/Gemini-API` commit remains temporarily as a quarantined
transport/parser adapter. It is not a source of truth for model capability.
Authenticated discovery evidence is required before a model route is used.
Unverified thinking levels and operations fail closed.

## Evidence and alternatives

| Option | Complexity | Reliability / compatibility | Migration and operations |
| --- | --- | --- | --- |
| Modernized Python (chosen) | Moderate | Protocol can change independently; typed validation and explicit revisions | No state migration or second runtime |
| Hybrid service | High | Same undocumented protocol, plus RPC/process failure modes | Adds image, health, deployment, and LXC overhead |
| Full rewrite | Very high | A different language does not make an undocumented protocol stable | Risks all 20 tools, auth, and persistent state |
| Keep patching the library | Low initially, high over time | Already failed for Flash and Pro; monkey patches couple every feature | Lowest immediate change, no recovery boundary |

The maintained upstream repository resolved to the already pinned commit
`4402e5231760a1e85e28eb6cd0040ff302b764de`. It therefore offered no newer
verified Flash/Pro route to adopt. Live protocol captures were not available in
this workspace, so this phase does not invent another selector.

## Consequences

- MCP names and arguments remain unchanged.
- Existing encrypted registry, profiles, cookies, and schema-v2 conversations
  are opened in place; no login is required by this change.
- New protocol adapters can replace the compatibility adapter without touching
  MCP tools or state formats.
- The legacy image/download path remains proxied during migration because it has
  specialized parsing not yet represented by a verified capture fixture.
- Flash and Pro are advertised as verified only when authenticated discovery
  provides a route. Passing serializer fixtures is not live verification.

## Security

Protocol errors expose categories and safe messages only. Contracts do not
contain cookies or auth tokens. Route details are excluded from public model
serialization. Account catalogs are keyed by account and never fall back to a
different account or model. Retries remain disabled for non-idempotent calls.
