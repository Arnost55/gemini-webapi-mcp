# Getting started

Gemini WebAPI MCP connects MCP clients to Gemini Web using isolated browser
sessions. It is not an official Google API client, and upstream web protocol
changes can affect operation.

## Hosted service (no server setup)

The project also provides a hosted MCP endpoint at
`https://gemini.proxmoxserver.dev/mcp` using Streamable HTTP. For this option,
you do not need to install the package, run Docker, provision a server, or
manage an encryption key. Add the endpoint to your remote MCP client, complete
the MCP OAuth authorization, and then complete Google sign-in in the isolated
browser handoff. Those authorization steps are still required; “no setup” means
no user-managed server deployment. The project operator hosts this service on
their own server. The account registry and durable OAuth state are encrypted;
see the [security guide](security.md) for the scope and key-handling details.
The service is intended to be available most of the time, but there is no 100%
uptime guarantee. Crashes, maintenance, network issues, and upstream outages
can interrupt access.

This hosted option is distinct from self-hosting. If you prefer to operate the
server and its storage yourself, continue with [installation](installation.md).

## Self-hosted setup

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
