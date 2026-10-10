FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GEMINI_PROFILE_ROOT=/var/lib/gemini-mcp/accounts
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src
RUN uv sync --no-dev --extra browser && uv run playwright install --with-deps chromium && useradd --create-home --uid 10001 --shell /usr/sbin/nologin gemini && mkdir -p /var/lib/gemini-mcp/accounts && chown -R gemini:gemini /app /var/lib/gemini-mcp
USER gemini
EXPOSE 8000
VOLUME ["/var/lib/gemini-mcp"]
CMD ["uv", "run", "gemini-webapi-mcp", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
