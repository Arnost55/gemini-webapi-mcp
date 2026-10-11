# Authentication architecture

```mermaid
sequenceDiagram
    participant C as ChatGPT/Codex
    participant M as MCP server
    participant V as Embedded RFB viewer
    participant B as Isolated Chromium
    participant G as Gemini Web
    C->>M: DCR and authorize with state + PKCE
    M->>B: create profile, display, and opaque handoff capability
    C->>V: open custom viewer page
    V->>B: authenticated WSS/RFB viewport
    B->>G: user completes Google login
    C->>M: completion code + state
    M->>M: validate state, readiness, and one-time code
    M->>M: register pending account and promote profile
    C->>M: exchange code with PKCE verifier
    C->>M: MCP request with bearer token
    M->>M: subject -> selected owned account -> profile cookies
    M->>G: account-scoped Gemini client
```

Dynamic registration creates a public PKCE client. It does not create a Google
credential and does not log the caller into Google. Each authorization creates a
distinct subject and account alias. A subject with several ready accounts must
select one explicitly through `gemini_select_account`.

The browser URL contains only the short-lived viewer capability. OAuth code,
state, and PKCE verifier remain in their respective OAuth flow and are never
used as an RFB token. The viewer is our own page and does not iframe Google or
the Linux desktop; noVNC's RFB client is used only as a protocol library.

The same page works as a top-level URL or as an iframe when its explicit
`GEMINI_VIEWER_FRAME_ANCESTORS` policy allows the embedding origin. The viewer
uses an account-bound handoff, polls only redacted status, and never receives
Gemini cookies, passwords, clipboard contents, or screenshots.
