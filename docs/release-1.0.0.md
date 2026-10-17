# 1.0.0 candidate: release, deployment, and recovery

This source tree is prepared as version `1.0.0`, but it is not an official
stable release yet. The candidate has no `v1.0.0` tag, GitHub Release, or
published container image. A user must confirm the new viewer in ChatGPT before
stable publication. This change does not deploy to production.

## Verified support and limitations

- Gemini chat, image generation/editing, private image artifacts, and the
  responsive image MCP App remain supported by the existing account-scoped
  architecture.
- Gemini Pro routing may fail after upstream protocol changes. Independent
  thinking-level mappings remain unverified.
- Gemini video generation is not exposed as a working MCP workflow. The
  validated-media/player components are not evidence of a generated video
  being retrieved or playable through ChatGPT.
- NotebookLM management, source ingestion, grounded chat, and Studio generation
  are not registered tools. Consumer access and protocol are independent from
  Gemini; Enterprise documentation does not imply consumer-account access.
- Google account entitlements, regional availability, rate limits, and web
  interface/protocol changes can affect behavior. Do not bypass those controls.
- This local Playwright test is not ChatGPT Android acceptance. The user must
  verify rendering and saving in the actual ChatGPT client.

## Build and immutable deployment reference

After reviewing and committing the candidate, obtain the full source commit SHA.
Do not substitute `latest` when testing or deploying this revision. Before a
version-tag workflow publishes an image, build the exact source locally:

```bash
git checkout --detach <FULL_COMMIT_SHA>
docker build \
  --build-arg VCS_REF=<FULL_COMMIT_SHA> \
  --build-arg APP_VERSION=1.0.0 \
  -t gemini-webapi-mcp:sha-<FULL_COMMIT_SHA> .
```

For a later, explicitly authorized version-tag release, the workflow publishes
the semantic version and immutable commit tags, for example:

```text
ghcr.io/arnost55/gemini-webapi-mcp:1.0.0
ghcr.io/arnost55/gemini-webapi-mcp:v1.0.0
ghcr.io/arnost55/gemini-webapi-mcp:sha-<FULL_COMMIT_SHA>
```

It intentionally does not create or move `latest`. A production compose file
should pin `image:` to the full `sha-<FULL_COMMIT_SHA>` tag and keep the current
environment, volume, and key mount. Bind MCP ports to loopback behind the
reviewed HTTPS proxy. Preserve the unprivileged-container Chrome/seccomp
requirements documented in [`install.md`](install.md); Docker runtime behavior
must be validated on the target Proxmox host before a later deployment.

No image for this candidate has been published by this change; do not pull the
example tag until the authorized release workflow has completed and verified
the baked commit/version metadata.

## Backup and rollback

Before any future production change, take and verify a backup of the existing
`gemini-state` volume and registry/encryption key using the deployment's
approved backup procedure. Keep the previous immutable image reference. Do not
delete/recreate the volume, reset accounts, or rotate/remove keys as part of a
code rollback.

To roll back, stop only the application container and recreate it with the
previous exact image SHA while reusing the unchanged volume, key, and
environment. Verify health, selected account diagnostics, and an existing
Gemini operation. If verification fails, stop and investigate; do not remove
the volume or credentials. This candidate has made no storage migration, so a
database downgrade is not expected.

## Troubleshooting

- **Image viewer still has scrollbars:** confirm the client resolved
  `ui://gemini-webapi-mcp/image-result-v2.html` (the v1 alias is retained),
  refresh the MCP app resource cache, and capture the actual client viewport.
  Local Chromium verifies constrained iframe geometry but not ChatGPT's own
  layout host.
- **Image download does not start:** the widget uses the ChatGPT file bridge
  when available. In other hosts, sandbox policies can block its best-effort
  anchor fallback; use the host-provided image save action where offered.
- **Gemini Pro or thinking mode differs:** inspect account/model capability
  diagnostics and report the exact supported model state. Do not infer that
  private protocol routes or thinking mappings remain stable.
- **Browser login fails in Docker/LXC:** check the host's approved seccomp
  profile, unprivileged LXC compatibility, browser channel, and sanitized
  service diagnostics. Do not use `--privileged` or `seccomp=unconfined`.
- **NotebookLM/video controls are requested:** report the current unsupported
  capability honestly; no fake job, notebook, or artifact is created.
