# Installation

The supported deployment focus is Docker Compose. The repository Compose file
is an example, not a universal production configuration: review its proxy URLs,
seccomp profile path, ports, and secrets before starting it. For the complete
Docker and Proxmox LXC notes, see [install.md](install.md).

## Requirements

- Docker Engine and Docker Compose v2, or Python 3.10+ with `uv` for local use.
- Persistent storage for `/var/lib/gemini-mcp`.
- A separately protected 32-byte registry key; do not store it in Git.
- For remote ChatGPT access, an HTTPS reverse proxy or trusted MCP tunnel.

## Compose quick start

Inspect and edit `docker-compose.yml` first. It expects `./registry.key`, a
host-provided Chrome seccomp profile, and example public URLs that must be
replaced. Generate the registry key with `uv run gemini-account-registry
keygen --output ./registry.key`, then protect it using the host's secret-file
permissions. Validate configuration before startup:

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 gemini-mcp
```

The example binds MCP to `127.0.0.1:8000` and persists application state in
the named `gemini-state` volume. The health endpoint is `/healthz`; it checks
the HTTP process, not Google authentication or browser readiness. Never expose
the raw MCP or VNC listener to the public internet.

## Published image

The `v1.0.0` workflow published image tags for source commit
`69b205d1a24c7eaaab69b9e2ca1222f78a042952`. Prefer the immutable reference:

```text
ghcr.io/arnost55/gemini-webapi-mcp:sha-69b205d1a24c7eaaab69b9e2ca1222f78a042952
```

Pin that tag in Compose and preserve the current volume, encryption key, and
environment when upgrading. See [release and recovery](release-1.0.0.md).

## Proxmox LXC

The production target may impose additional Chrome sandbox and seccomp
constraints. Validate the reviewed host seccomp profile and unprivileged LXC
behavior on a staging host first. Do not solve startup problems with
`--privileged` or `seccomp=unconfined`; see [install.md](install.md).

For local STDIO operation, use `uv sync` and
`uv run gemini-webapi-mcp --transport stdio`. A local Google browser session is
separate from the server's MCP OAuth and account selection.
