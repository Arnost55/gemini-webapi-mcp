# Development

Requirements: Python 3.10+ and `uv`. Install dependencies, run tests and lint,
and build the package:

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv build
git diff --check
```

Browser UI tests use the project's optional browser dependencies and Playwright
Chromium; follow the test configuration in `pyproject.toml`. A local Chromium
result does not verify ChatGPT host behavior. Do not record cookies, private
account data, or notebook contents in fixtures. Keep new upstream integration
protocols behind replaceable adapters and add sanitized fixtures for verified
behavior. Run `docker compose config` after modifying the deployment example.

Pull requests should preserve the existing account, artifact, and OAuth
ownership boundaries and should describe which tests are unit, browser, CI, or
actual client acceptance tests. See [security](security.md).
