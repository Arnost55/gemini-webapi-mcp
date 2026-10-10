# Authentik gateway

Authentik should sit in front of the MCP listener and provide the OAuth/OIDC
identity used by ChatGPT or Codex. Gemini Web login remains separate: the
selected isolated Chromium profile still needs its own Google session.

```text
ChatGPT/Codex -> https://gemini.proxmoxserver.dev
              -> Authentik OAuth/OIDC gateway
              -> http://127.0.0.1:8000/mcp
              -> Gemini MCP
```

Create an Authentik OAuth2/OpenID provider and application for the gateway.
Use a confidential client, PKCE with S256, and a callback owned by the gateway:

```text
https://gemini.proxmoxserver.dev/oauth/callback
```

Configure the gateway with:

```env
GEMINI_PUBLIC_URL=https://gemini.proxmoxserver.dev
AUTHENTIK_ISSUER=https://auth.example.com/application/o/gemini-mcp/
AUTHENTIK_CLIENT_ID=<client-id>
AUTHENTIK_CLIENT_SECRET=<secret-from-secret-manager>
AUTHENTIK_SCOPES=openid profile email
```

The gateway must publish protected-resource metadata at:

```text
https://gemini.proxmoxserver.dev/.well-known/oauth-protected-resource
```

Authentik's issuer provides the authorization-server metadata through its
OpenID configuration endpoint:

```text
<AUTHENTIK_ISSUER>/.well-known/openid-configuration
```

Authentik authenticates the caller; it does not create or transfer Google
Gemini cookies. After Authentik login, the gateway starts the one-time browser
handoff and the user approves the Gemini profile connection. Keep Authentik
secrets, registry keys, and browser profiles outside Git.
