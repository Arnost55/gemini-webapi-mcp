# Gemini WebAPI MCP

An MCP server for Gemini Web chat, file analysis, image generation, and account-scoped browser sessions. It uses the Gemini Web experience rather than a Google AI API key; upstream web interfaces and account access can change.

> **Release state:** `v1.0.0` is tagged and the GHCR workflow completed successfully for commit `69b205d1a24c7eaaab69b9e2ca1222f78a042952`. The workflow also pulled the published immutable image and passed provenance and startup checks. No production deployment is implied. ChatGPT Android acceptance of the image viewer has not been confirmed by the user.

## Contents

- [Features and support](#features-and-support)
- [Quick start](#quick-start)
- [ChatGPT connection](#chatgpt-connection)
- [Tools](#mcp-tools)
- [Security and storage](#security-and-storage)
- [Development](#development)
- [Documentation](#documentation)
- [License](#license)

## Features and support

| Capability | Current status |
|---|---|
| Gemini Flash / Flash-Lite chat, persistent conversations, file and URL analysis | Implemented; availability depends on the selected account and upstream client |
| Model discovery | Implemented; returns observed/fallback confidence and routability metadata |
| Gemini image generation/editing | Implemented through `gemini_generate_image`; browser-protocol dependency may change |
| Image MCP App | Responsive natural-ratio gallery, zoom/fullscreen, download, regenerate/edit actions; local Chromium layout tests pass; actual ChatGPT host behavior varies |
| Gemini Pro routes and independent thinking controls | Not verified as reliable; see [models](docs/models.md) |
| Gemini video generation | Not implemented as an MCP workflow. Browser observation and a validated-media player component do not constitute generation or retrieval support |
| NotebookLM | Capability report only. Notebook management, source upload, grounded chat, and Studio generation are unsupported |

The image and video UI resources are `ui://gemini-webapi-mcp/image-result-v4.html` (with v1/v2/v3 compatibility) and `ui://gemini-webapi-mcp/video-result-v1.html`. Video artifacts can be validated and represented privately, but no tool currently generates them. No genuine, authorized ChatGPT screenshot is included in this repository; add one only after obtaining and reviewing an actual capture.

## Quick start

### Use the hosted service (no server setup)

The project also provides a hosted instance. You do not need to install Python,
Docker, configure a server, or manage a registry key to use it. Add
`https://gemini.proxmoxserver.dev/mcp` as a remote MCP server using Streamable
HTTP, complete the MCP authorization flow, and then sign in to Google through
the isolated browser handoff when prompted. MCP authorization and Google
sign-in are separate first-use steps. Hosted availability and account access
are subject to the operator's service and Google's policies; this documentation
does not promise an uptime level or bypass Google entitlements. See
[ChatGPT integration](docs/chatgpt-integration.md).

### Self-host

Requirements: Docker Compose v2, a persistent writable data volume, and a separately protected 32-byte registry key. The repository Compose example includes a host-specific seccomp path and HTTPS URLs; review and change those values for your deployment before starting it.

```bash
uv run gemini-account-registry keygen --output ./registry.key
docker compose config
docker compose up -d --build
docker compose logs -f gemini-mcp
```

Compose binds the MCP port to loopback (`127.0.0.1:8000`) and stores state in `gemini-state`. Put a reviewed TLS reverse proxy or secure tunnel in front for remote clients; do not expose the raw listener or VNC port publicly. For the published image, see [installation](docs/installation.md) and pin the immutable tag:

```text
ghcr.io/arnost55/gemini-webapi-mcp:sha-69b205d1a24c7eaaab69b9e2ca1222f78a042952
```

## ChatGPT connection

For remote use, configure the HTTPS MCP endpoint at `https://<your-host>/mcp` using Streamable HTTP and complete the server's OAuth 2.1/PKCE flow. `GEMINI_PUBLIC_URL` must be the public HTTPS origin. This OAuth authenticates the MCP client to this server; it does **not** sign in to Google. Gemini Web login is completed separately in an isolated browser handoff. ChatGPT feature availability and MCP App presentation differ by platform and client version.

After connecting, select an authorized account if prompted, then try:

- “List my connected Gemini accounts and show their status.”
- “Ask Gemini to explain this topic using Flash.”
- “Generate an image of … in a portrait aspect ratio.”
- “Analyze this PDF and summarize its key points.”

See [ChatGPT integration](docs/chatgpt-integration.md), [authentication](docs/authentication.md), and [gateway deployment](docs/gateway.md).

## MCP tools

| Group | Registered tools |
|---|---|
| Accounts and server | `gemini_list_accounts`, `gemini_account_diagnostics`, `gemini_get_account_info`, `gemini_select_account`, `gemini_deactivate_account`, `gemini_delete_account`, `gemini_server_info` |
| Capabilities and models | `gemini_list_models`, `gemini_video_capabilities`, `notebooklm_capabilities` |
| Chat and conversations | `gemini_chat`, `gemini_start_chat`, `gemini_list_chats`, `gemini_get_chat`, `gemini_get_chat_history`, `gemini_delete_chat`, `gemini_switch_model`, `gemini_reset` |
| Media and analysis | `gemini_generate_image`, `gemini_upload_file`, `gemini_upload_file_bytes`, `gemini_analyze_url` |

The server also registers image (v2 and legacy v1) and video MCP App resources. The video resource is presentation infrastructure only; no Gemini video generation tool is registered. See [the feature matrix](docs/feature-matrix.md) for details.

## Security and storage

The account registry and durable OAuth state are encrypted with AES-256-GCM. Google browser cookies remain in isolated persistent Chromium profiles and are not returned in MCP results. Account and artifact ownership are validated; generated images are delivered as native MCP image content, and private media metadata does not expose storage paths. Protect the encryption key separately from the persistent volume and back them up together. Never commit `registry.key`, cookies, browser profiles, or real account data.

Read [security](docs/security.md), [backup and restore](docs/backup-and-restore.md), and [the security threat model](docs/security-threat-model.md) before exposing a remote gateway.

## Development

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv build
```

For browser-layout tests, install the optional extra and Chromium as described in [development](docs/development.md). Changes to undocumented Google protocols require evidence and tests; do not infer endpoints or bypass access restrictions.

## Documentation

- [Getting started](docs/getting-started.md) · [Installation](docs/installation.md) · [Configuration](docs/configuration.md)
- [Authentication](docs/authentication.md) · [ChatGPT integration](docs/chatgpt-integration.md) · [Models](docs/models.md)
- [Image generation](docs/image-generation.md) · [Media viewer](docs/media-viewer.md) · [Video status](docs/video.md) · [NotebookLM status](docs/notebooklm.md)
- [Client compatibility](docs/client-compatibility.md)
- [Architecture](docs/architecture.md) · [Security](docs/security.md) · [Troubleshooting](docs/troubleshooting.md) · [Backup and restore](docs/backup-and-restore.md)
- [Development](docs/development.md) · [Release process](docs/release-process.md) · [v1.0.0 release notes](docs/release-1.0.0.md) · [Changelog](CHANGELOG.md)

## License

Licensed under [GNU Affero General Public License v3 or later](LICENSE) (AGPL-3.0-or-later). The code/package metadata and repository license are authoritative.
