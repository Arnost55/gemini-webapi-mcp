# Gemini Web MCP Server

Free hosted and self-hostable [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
server for Google Gemini Web. Connect a compatible MCP client to chat with
Gemini, analyze files and URLs, generate or edit images, and manage
account-scoped conversations. Use the no-code hosted endpoint or run your own
Python server over STDIO or Streamable HTTP; Docker Compose is provided for
self-hosting.

This project automates Gemini Web through browser sessions; it is not a client
for the official Gemini API. Google can change its web interface, protocol,
account availability, and rate limits at any time. The hosted MCP service is
free to use; third-party account or client fees are separate.

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/Protocol-MCP-5B5FC7.svg)](https://modelcontextprotocol.io/)

## Choose how to connect

| Option | Best for | Setup |
|---|---|---|
| Hosted service | Free, plug-and-play access | Connect your MCP client to [`https://gemini.proxmoxserver.dev/mcp`](https://gemini.proxmoxserver.dev/mcp). No coding, local installation, or server setup required. |
| Local STDIO | A client and server on the same computer | Install with `uv`, then launch `gemini-webapi-mcp --transport stdio`. |
| Self-hosted Streamable HTTP | A server used by remote MCP clients | Run the Compose example behind a reviewed HTTPS proxy or secure tunnel. |

See the [documentation index](docs/README.md) for step-by-step setup, client
compatibility, configuration, security, operations, and development guides.

The hosted MCP service is free to use. The project operator runs it on their
own server, with encrypted account registry and durable OAuth state; see the
[security guide](docs/security.md). You still need an MCP client and a Google
account with access to Gemini. Any fees or limits imposed by Google or your MCP
client are separate. The service is intended to be up most of the time, but
has no 100% uptime guarantee and may be interrupted by crashes, maintenance,
network issues, or upstream outages.

## Local quick start

Requirements: Python 3.10 or newer and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Arnost55/gemini-webapi-mcp.git
cd gemini-webapi-mcp
uv sync
uv run gemini-webapi-mcp --transport stdio
```

Configure your MCP client to launch that command from the checkout. Sign in to
Gemini Web through the supported browser-session setup. Google browser sign-in
is separate from MCP client authorization. See [getting started](docs/getting-started.md)
and [installation](docs/installation.md).

## Self-host with Docker Compose

The checked-in [`docker-compose.yml`](docker-compose.yml) is a deployment
example that must be reviewed for your host. It expects a registry key at
`./registry.key`, a host Chrome seccomp profile at the configured path, and
example HTTPS origins. Replace those values before starting the service.

```bash
uv sync
uv run gemini-account-registry keygen --output ./registry.key
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f gemini-mcp
```

The MCP and noVNC ports bind to loopback. Keep them behind a reviewed proxy or
trusted tunnel, and do not expose raw MCP/VNC listeners publicly. Persistent
account profiles, encrypted registry state, and OAuth state live in the
`gemini-state` volume. Back up the volume and encryption key together. Read the
[installation](docs/installation.md), [gateway](docs/gateway.md),
[security](docs/security.md), and [backup and restore](docs/backup-and-restore.md)
guides before remote use.

To run a published image, use an immutable commit reference from the
[installation guide](docs/installation.md) and review the current Compose
configuration first.

## Supported features

| Feature | Status | Notes |
|---|---|---|
| Gemini Flash and Flash-Lite chat | Implemented | Account entitlements and upstream behavior apply. |
| Model discovery | Implemented | Reports observed and fallback metadata; discovery does not guarantee a route works. |
| Persistent conversations | Implemented | Account-scoped conversation management. |
| File upload and URL analysis | Implemented | See the registered tool list and feature matrix. |
| Gemini image generation and editing | Implemented | Uses Gemini Web; upstream and account availability apply. |
| MCP image content | Implemented | Returns standard MCP text and image content. A client may choose not to display images. |
| Optional image viewer | Implemented | Responsive viewer tested in a local browser; rendering, fullscreen, and download behavior vary by MCP host. |
| Gemini Pro routing and independent thinking levels | Unverified | See [model support](docs/models.md). |
| Gemini video generation | Unsupported | No video generation MCP tool or verified artifact retrieval workflow is registered. |
| NotebookLM workflows | Unsupported | The capability report is informational; notebook management, source upload, grounded chat, and Studio tools are not registered. |

See the detailed [feature matrix](docs/feature-matrix.md) and
[client compatibility matrix](docs/client-compatibility.md). No real Gemini MCP
viewer screenshot is included: add one only after capturing it from an
authorized, real client session and checking it for private data.

### Known image viewer limitations

Image downloads may fail in embedded MCP clients. The viewer uses a host-provided
download bridge when available and a browser download fallback otherwise; some
client sandboxes block that fallback. If the download button does nothing, use
the MCP client's own image save action. Fullscreen, regenerate, and edit actions
also depend on the client exposing the required MCP App APIs, so those controls
may be unavailable or unresponsive in some hosts. Local browser coverage does
not guarantee identical behavior in every MCP client. See the [viewer
troubleshooting notes](docs/media-viewer.md#known-client-dependent-issues) and
[client compatibility matrix](docs/client-compatibility.md).

## MCP tools

| Area | Tools |
|---|---|
| Accounts and server | `gemini_list_accounts`, `gemini_account_diagnostics`, `gemini_get_account_info`, `gemini_select_account`, `gemini_deactivate_account`, `gemini_delete_account`, `gemini_server_info` |
| Models and capability reports | `gemini_list_models`, `gemini_video_capabilities`, `notebooklm_capabilities` |
| Chat and conversations | `gemini_chat`, `gemini_start_chat`, `gemini_list_chats`, `gemini_get_chat`, `gemini_get_chat_history`, `gemini_delete_chat`, `gemini_switch_model`, `gemini_reset` |
| Files, URLs, and images | `gemini_upload_file`, `gemini_upload_file_bytes`, `gemini_analyze_url`, `gemini_generate_image` |

The server also exposes optional MCP App resources. The video viewer resource is
presentation infrastructure only and does not add video generation support.

## Compatibility

The server supports MCP STDIO and Streamable HTTP transports. Image results use
standard MCP `TextContent` and `ImageContent`; the MCP App gallery is an optional
host extension. Protocol delivery, client ingestion, inline image display, and
viewer rendering are separate behaviors. Do not infer client support from a
valid server response. See the tested [client matrix](docs/client-compatibility.md).

## Security and privacy

- Google sign-in runs in isolated browser profiles. Browser cookies are not
  returned in MCP results.
- The account registry and durable OAuth state use AES-256-GCM encryption.
  Protect and back up the encryption key separately from the volume.
- Keep registry keys, browser profiles, cookies, real account data, and OAuth
  state out of Git, logs, tickets, and screenshots.
- MCP OAuth authorizes a client to this server; it does not sign in to Google
  or grant Google account entitlements.

Read the [security guide](docs/security.md) and
[threat model](docs/security-threat-model.md) before exposing a gateway.

## Development

```bash
uv sync --group dev
uv run pytest -q
uv build
```

See [development](docs/development.md) and [contributing](CONTRIBUTING.md).
Changes to undocumented Google protocols need captured evidence and regression
coverage. Do not infer private endpoints or bypass Google access controls.

## Project status

The repository already contains a `v1.0.0` tag and recorded container
publication. This documentation update does not create or move a tag, publish
a GitHub Release, or deploy production. See the [changelog](CHANGELOG.md) and
[release notes](docs/release-1.0.0.md) for the recorded release state.

## License

Licensed under the [GNU Affero General Public License v3.0 or later](LICENSE)
(AGPL-3.0-or-later).
