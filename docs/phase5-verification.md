# Phase 5 verification record

## Automated

The suite covers serializer binding, account-scoped exact model routing,
capability fail-closed behavior, independent thinking validation, stream order,
stall/completion checks, error classification, state-copy safety, existing MCP
contracts, account isolation, auth handoff, conversations, uploads, and images.

## Live

Not run: this workspace contains no authorized staging account/profile. No claim
is made that Flash, Pro, image generation, or thinking levels work live. The
implementation deliberately reports unverified capability instead of mapping to
Flash-Lite or fabricating a control.

## Known limitations

- The compatibility adapter still relies on an undocumented upstream parser and
  the captured StreamGenerate revision.
- Image generation retains its specialized compatibility path and remains
  unverified until an authorized live matrix succeeds.
- Independent low/medium/high thinking is unavailable unless discovery/config
  contains cryptographically fingerprinted selector evidence.
- Google can change its private protocol without notice. Adapter and serializer
  revision diagnostics are intended to make that failure bounded and visible.

## Architecture comparison

| Area | Previous architecture | New architecture | Improvement / evidence |
| --- | --- | --- | --- |
| Gemini protocol | Customized library plus server monkey patches | Versioned typed adapter boundary; legacy client quarantined | Pure serializer tests and replaceable adapter contract |
| Model routing | Discovery and routing split across fallbacks and aliases | Exact account-scoped registry | Cross-account and no-substitution tests |
| Streaming | Library parser state coupled to generation | Explicit order, progress, stall, and completion state | Deterministic stream state-machine tests |
| Thinking levels | Unsupported/inferred | `auto` plus only evidence-backed explicit levels | Fail-closed registry tests |
| Image generation | Specialized, unverified patches | Preserved compatibility path, advertised only when verified | No false live claim; existing image tests remain green |
| Authentication | Chrome/Xvfb/noVNC and encrypted registry | Preserved unchanged behind adapter | Existing auth, registry, and handoff tests remain green |
| Persistent storage | AES-GCM registry v1 and SQLite v2 | Same formats; read-only preflight and exclusive-copy rehearsal | Copy/source-integrity test; zero production migration |
| Observability | Generic errors and serializer note | Stable error categories plus adapter/serializer/build revisions | Public diagnostics exclude routes, prompts, and credentials |
