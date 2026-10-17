FROM python:3.12-slim
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
ARG APP_VERSION=unknown
LABEL org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.created=$BUILD_DATE \
      org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.source="https://github.com/Arnost55/gemini-webapi-mcp"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GEMINI_PROFILE_ROOT=/var/lib/gemini-mcp/accounts GEMINI_BROWSER_CHANNEL=chrome GEMINI_CHROMIUM_SANDBOX=true GEMINI_BUILD_COMMIT=$VCS_REF GEMINI_BUILD_TIMESTAMP=$BUILD_DATE GEMINI_BUILD_VERSION=$APP_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git xvfb fluxbox x11vnc x11-utils novnc websockify && \
    curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/google-chrome.deb && \
    apt-get install -y --no-install-recommends /tmp/google-chrome.deb && \
    rm -f /tmp/google-chrome.deb && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN uv sync --no-dev --extra browser && useradd --create-home --uid 10001 --shell /usr/sbin/nologin gemini && mkdir -p /var/lib/gemini-mcp/accounts && chown -R gemini:gemini /app /var/lib/gemini-mcp
USER gemini
EXPOSE 8000 6080
VOLUME ["/var/lib/gemini-mcp"]
CMD ["/usr/local/bin/docker-entrypoint.sh"]
