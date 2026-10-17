# Changelog

## 1.0.0 candidate — not yet released

- Fixed MCP image viewer sizing in capped/sandboxed iframes; image fit now
  follows actual image and available viewport dimensions without distortion.
- Improved expanded image fit, centering, bounded zoom scrolling, and responsive
  resizing while retaining the v1 resource URI and private `data:` preview path.
- Added real Chromium regression coverage for 15 viewport/aspect-ratio pairs,
  gallery navigation, fullscreen entry/exit, zoom reset, download fidelity, and
  decode errors.
- Prepared the package/runtime version as 1.0.0 and retained build commit,
  timestamp, version, BuildKit provenance, and SBOM metadata.
- Restricted container publication to `v*` tags and version/SHA tags. Normal
  `main` pushes and pull requests build and smoke-test locally; no `latest` tag
  is produced or changed by this workflow.
- No Gemini video-generation MCP workflow or NotebookLM management/source/chat/
  Studio tools are claimed. Their protocols and account access remain unverified
  for safe consumer MCP integration.
- No `v1.0.0` tag, GitHub Release, registry publication, or production deploy
  has been made by this candidate change.

## 0.2.0

See Git history for earlier changes.
