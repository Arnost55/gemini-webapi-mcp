# Contributing

Bug reports, focused fixes, and documentation improvements are welcome. For a
large change, open an issue first to agree on scope. Keep pull requests focused
and explain the behavior change, evidence, and user impact.

## Before changing code

- Read the [feature matrix](docs/feature-matrix.md), [client compatibility
  matrix](docs/client-compatibility.md), and relevant design notes in
  [the documentation index](docs/README.md).
- Check current upstream behavior before changing Gemini Web protocol handling.
  Include sanitized observations and a regression test; do not guess endpoints,
  request fields, or account entitlements.
- Keep authentication, account isolation, encryption, and private media
  ownership boundaries intact. Never include cookies, tokens, keys, account
  data, prompts, or generated private media in a contribution.
- Describe support conservatively. A capability report, browser observation,
  local rendering, or valid MCP payload does not establish another one of those
  layers as working.

## Set up and verify

Use Python 3.10+ and `uv`:

```bash
uv sync --group dev
uv run pytest -q
uv build
```

If Ruff is installed in your development environment, also run:

```bash
uv run ruff check .
```

For browser-viewer changes, follow [development](docs/development.md) to install
the optional browser dependency and run the relevant Chromium coverage. For
container or deployment changes, validate the Compose configuration and
describe what was and was not exercised. Do not expose production credentials
or start a production deployment as part of local verification.

## Documentation changes

- Put user-facing operational and feature documentation in `docs/`, and add it
  to [the documentation index](docs/README.md).
- Keep the README concise enough to scan; link to detailed procedures.
- Verify relative links, shell commands, environment names, image references,
  and examples against the current source and workflow.
- Label unverified client behavior and unsupported features explicitly.
- Use screenshots only when captured from an authorized real client session;
  review them for account details, prompts, tokens, and other private data.

## Pull request checklist

- [ ] The change is focused and its rationale is clear.
- [ ] Relevant tests or documentation checks pass; any skipped checks are listed.
- [ ] No credentials, private browser state, or user data are included.
- [ ] Support claims match tested behavior and the compatibility matrix.
- [ ] User-facing docs and the changelog are updated when behavior changes.
- [ ] Release tags, GitHub Releases, and production deployments are left to the
  explicitly authorized release process.
