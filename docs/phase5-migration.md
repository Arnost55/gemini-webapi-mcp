# Phase 5 migration, deployment, and rollback

## Compatibility

This release is an in-place application upgrade. It does not change the
encrypted registry envelope (AES-256-GCM v1), registry key, browser profile
layout, cookie storage, MCP tool names/arguments, media directory, or SQLite
conversation schema (v2). No production data migration is required.

Before deployment, stop the application and make storage-level snapshots of
the Docker volume and registry key. Do not copy a live Chromium profile while
Chrome is writing it. The optional `state_migration.inspect_state` helper is
read-only. `copy_for_migration` creates an exclusive synthetic copy and refuses
to overwrite an existing destination; use it for rehearsal only.

## Staged deployment

1. Build an immutable image with `VCS_REF` set to the reviewed commit.
2. On a copy of the volume, verify registry decryption, schema version 2,
   account listing, conversation listing, and browser-profile detection.
3. Run the mocked suite. With authorized staging credentials only, run the live
   matrix below and record account, model public ID, adapter revision, outcome,
   and timestamp. Never record cookies or prompt/response content.
4. Replace only the staging container. Confirm `/healthz` and
   `gemini_server_info`, then exercise each MCP contract.
5. Deploy to production only after explicit authorization.

## Live matrix

Flash-Lite, Flash, Pro, continuation, upload, image generation/editing, model
switching, and each advertised thinking level must be tested independently.
Results are capability evidence only for that account and protocol revision.
An unavailable or untested row remains `unknown`/`discovered`, not `verified`.

## Rollback

Stop the new container and start the prior immutable image against the unchanged
volume. Because Phase 5 performs no state write migration, application rollback
requires no data rollback. If later versions introduce a schema, restore the
pre-deployment volume snapshot rather than downgrading an opened database.

## Resource impact

The boundary adds small Pydantic objects and per-account in-memory snapshots.
It adds no process, port, browser, database, or background task. Expected CPU
and memory impact is negligible compared with Chrome and image processing.
