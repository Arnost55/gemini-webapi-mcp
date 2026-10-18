# Documentation

Use this index to find setup instructions, supported-feature details, and
operational guidance for Gemini WebAPI MCP. Historical phase notes and
architecture decisions remain available for implementation context.

## Start here

- [Getting started](getting-started.md) — first connection and basic workflow.
- [Installation](installation.md) — local, hosted, and Docker Compose options.
- [Configuration](configuration.md) — environment variables and service settings.
- [Authentication](authentication.md) — MCP authorization and Google browser sign-in.
- [ChatGPT integration](chatgpt-integration.md) — connect a remote MCP client.

## Capabilities and compatibility

- [Feature matrix](feature-matrix.md) — implemented, unverified, and unsupported workflows.
- [Client compatibility](client-compatibility.md) — protocol checks versus client UI acceptance.
- [Model support](models.md) — model discovery, routing, and known limitations.
- [Image generation](image-generation.md) — prompts, output, and image editing.
- [Media viewer](media-viewer.md) — optional MCP App behavior and tested controls.
- [Video status](video.md) — current limits; no video generation MCP workflow is registered.
- [NotebookLM status](notebooklm.md) — capability-report scope and unsupported workflows.

## Operate securely

- [Gateway deployment](gateway.md) — Streamable HTTP, OAuth, browser handoff, and proxy guidance.
- [Authentication architecture](authentication-architecture.md) — account isolation and durable authorization state.
- [Authentik integration](authentik.md) — identity-provider deployment notes.
- [Security](security.md) and [threat model](security-threat-model.md) — secrets, trust boundaries, and risks.
- [Backup and restore](backup-and-restore.md) — preserve encrypted state and browser profiles.
- [Troubleshooting](troubleshooting.md) — diagnose common setup and runtime issues.
- [Recovery runbook](recovery-runbook.md) — operator recovery procedures.

## Develop and release

- [Architecture](architecture.md) — high-level components and data flow.
- [Development](development.md) — local environment and verification commands.
- [Release process](release-process.md) — release verification and authorization boundaries.
- [v1.0.0 release notes](release-1.0.0.md) — recorded tag, image, scope, and limitations.
- [Contributing](../CONTRIBUTING.md) — change proposals, evidence, and review expectations.
- [Changelog](../CHANGELOG.md) — unreleased changes and version history.

## Design and investigation notes

These files record project-specific audits, evidence, and decisions. They are
not installation guides or promises of current upstream compatibility.

- [Protocol isolation ADR](adr-005-protocol-isolation.md)
- [Browser login acceptance](browser-login-acceptance.md)
- [Phase 5.3](phase-5.3.md), [Phase 5 migration](phase5-migration.md), and [Phase 5 verification](phase5-verification.md)
- [Phase 5.1 browser evidence](phase5.1-browser-evidence.md) and [Phase 5.2 media findings](phase5.2-media-and-browser-findings.md)
- [Phase 4 upstream audit](phase4-upstream-audit.md), [4.1 protocol recovery](phase4.1-protocol-recovery.md), [4.3 audit](phase4.3-audit.md), [4.4 compatibility](phase4.4-library-compatibility.md), and [4.5 protocol recovery](phase4.5-live-protocol-recovery.md)
