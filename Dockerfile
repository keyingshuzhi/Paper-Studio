# syntax=docker/dockerfile:1
FROM python:3.13-slim AS runtime

ARG APP_VERSION=0.1.1

LABEL org.opencontainers.image.title="Paper Studio" \
      org.opencontainers.image.description="Local-first AI research workspace" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/keyingshuzhi/Paper-Studio"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PAPER_STUDIO_DATA_DIR=/data \
    PAPER_STUDIO_CONFIG_DIR=/config

WORKDIR /app

# Use the same uv-managed dependency lock as local development.
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY agent ./agent
COPY docker-entrypoint.sh /usr/local/bin/paper-studio-entrypoint

RUN apt-get update \
    && apt-get install --no-install-recommends -y gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 paperstudio \
    && useradd --uid 10001 --gid paperstudio --create-home --shell /usr/sbin/nologin paperstudio \
    && mkdir -p /data /config \
    # Source files may retain restrictive local permissions when copied into
    # the build context, so make the application tree readable by its runner.
    && chown -R paperstudio:paperstudio /app/agent /data /config \
    && chmod 0755 /usr/local/bin/paper-studio-entrypoint

EXPOSE 8765
VOLUME ["/data", "/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/', timeout=3).close()"]

ENTRYPOINT ["/usr/local/bin/paper-studio-entrypoint"]
CMD ["/app/.venv/bin/python", "-B", "-m", "agent.webapp", "--host", "0.0.0.0", "--port", "8765"]
