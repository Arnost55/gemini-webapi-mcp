# Authentication architecture

```mermaid
sequenceDiagram
    participant C as ChatGPT/Codex
    participant M as MCP server
    participant B as Isolated Chromium/noVNC
    participant G as Gemini Web
    C->>M: DCR and authorize with state + PKCE
    M->>B: create profile, display, and VNC capability
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

The browser URL contains only the short-lived noVNC capability. OAuth code,
state, and PKCE verifier remain in their respective OAuth flow and are never
used as a VNC token.
