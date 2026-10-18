# Changelog

## Unreleased

- Improved GitHub search metadata and the project introduction with Gemini Web,
  MCP transport, Docker Compose, file analysis, and image-generation keywords.
- Reworked the GitHub README around accurate Gemini Web authentication,
  supported MCP features, client compatibility, and self-hosted Compose setup.
- Added a navigable documentation index and contributor guide; documented the
  verified feature boundaries without claiming video or NotebookLM workflows.
- Clarified that the project-hosted MCP endpoint runs on the operator's server,
  which state is encrypted, and that service availability is best effort.
- Highlighted the free hosted option and no-code, plug-and-play connection path.
- Fixed expanded image overlays persisting after the MCP host exits fullscreen;
  downloads in clients without a host bridge now use a Blob URL. Published the
  cache-busted `v4` viewer resource while retaining v1/v2/v3 compatibility.
- Fixed a viewer height feedback loop that could collapse generated images to
  thumbnail size in initially short host iframes.
- Moved download and fullscreen controls into an in-flow responsive toolbar,
  preserved width-driven natural image sizing, and published a cache-busting
  `v3` viewer resource while retaining the `v1` and `v2` compatibility URIs.
- Documented the standards-first image delivery contract and a client
  compatibility matrix that separates protocol receipt from UI rendering.
- Added host-profile regression coverage proving native image blocks remain
  available when clients ignore MCP Apps metadata or structured results.
- Declared MCP App tool visibility explicitly without making the viewer a
  dependency of image generation.

## [1.0.0] - 2026-10-17

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
- The `v1.0.0` tag exists and the tag-triggered GHCR workflow completed
  successfully for source commit `69b205d1a24c7eaaab69b9e2ca1222f78a042952`.
  No production deployment is implied. A GitHub Release page is a separate
  publication step and is not claimed here.

## 0.2.0

See Git history for earlier changes.
