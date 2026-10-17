# Getting started

Gemini WebAPI MCP connects MCP clients to Gemini Web using isolated browser
sessions. It is not an official Google API client, and upstream web protocol
changes can affect operation.

1. Follow [installation](installation.md) and protect the registry key and
   persistent `gemini-state` volume.
2. Configure a public HTTPS origin in `GEMINI_PUBLIC_URL` if using remote
   Streamable HTTP.
3. Connect the MCP client to `https://<your-host>/mcp` and complete the server's
   OAuth 2.1/PKCE authorization.
4. Separately complete Google login in the account's isolated browser handoff.
5. Select the intended account and test a simple Gemini chat, then image
   generation if enabled for the account.

MCP OAuth identifies the client to this server; it does not sign into Google.
Use [ChatGPT integration](chatgpt-integration.md) and
[authentication](authentication.md) for the full flow. ChatGPT Android image
viewer acceptance remains a client-side check and must not be inferred from
local browser tests.
