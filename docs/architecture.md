# Architecture

The application exposes MCP over STDIO or Streamable HTTP and delegates Gemini
operations to isolated browser-backed adapters. Account selection and policy
are separate from the upstream protocol layer. The encrypted account registry
stores account metadata; Chromium profiles retain Google browser sessions.
Persistent conversations and media artifacts use private server-side storage.

The gateway OAuth boundary authenticates MCP callers; it is distinct from
Google login. Media ownership is account-scoped. Images are returned as native
MCP image content and rendered by versioned MCP App resources. Video UI and
artifact validation exist as reusable infrastructure but do not make video
generation operational. NotebookLM has capability reporting only and is not
tightly coupled to Gemini's private request adapter.

For design decisions and protocol boundaries, see
[ADR-005](adr-005-protocol-isolation.md),
[authentication architecture](authentication-architecture.md), and the
[security threat model](security-threat-model.md).
