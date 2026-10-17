# Troubleshooting

Start with read-only checks and preserve the persistent volume and key.

| Symptom | Safe checks |
|---|---|
| MCP cannot connect | Check `docker compose ps`, proxy HTTPS route, Streamable HTTP `/mcp`, and OAuth metadata; inspect sanitized logs. |
| `/healthz` fails | Check container logs and Compose configuration; health verifies HTTP process availability only. |
| Google login required | Re-open the intended isolated browser handoff and complete sign-in; do not copy cookies or reset the registry. |
| Model unavailable / Pro rejected | Check account diagnostics and `gemini_list_models`; Pro and thinking mappings are not guaranteed. |
| Image generation fails | Check account readiness, upstream error category, upload constraints, and whether the image workflow is available to that account. |
| Viewer blank or clipped | Confirm v2 resource (v1 alias retained), refresh host resource cache, inspect actual iframe dimensions and CSP/network diagnostics. |
| Download fails | Host sandbox/bridge support may differ; use the host's save action if present and retain original MCP image content. |
| Chrome fails in LXC | Review host seccomp profile, browser availability, permissions, and sanitized startup diagnostics. Do not use privileged mode or `seccomp=unconfined`. |
| Docker volume permission error | Inspect mount ownership and container user; back up before any permission change. |
| Provenance mismatch | Compare baked revision/version metadata to full immutable SHA and the successful Actions run; do not trust a mutable tag. |
| Video or NotebookLM operation requested | Check capability tools; generation and operational NotebookLM tools are unsupported. |

Useful non-destructive commands:

```bash
docker compose config
docker compose ps
docker compose logs --tail=100 gemini-mcp
docker inspect --format '{{.State.Health.Status}}' gemini-mcp
```

Never remove `gemini-state`, registry keys, profiles, or conversations as an
ordinary troubleshooting step. See [recovery runbook](recovery-runbook.md).
