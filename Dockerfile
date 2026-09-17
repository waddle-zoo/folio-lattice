FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv

FROM python:3.12.13-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision="$VCS_REF" \
      org.opencontainers.image.source="https://github.com/waddle-zoo/folio-lattice"

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable \
    && rm -rf /root/.cache/uv

RUN useradd --create-home --uid 10001 folio \
    && mkdir -p /data \
    && chown -R folio:folio /app /data
USER folio

ENV FOLIO_DB_PATH=/data/folio.db \
    FOLIO_BLOB_ROOT=/data/blobs \
    FOLIO_TENANT_ID=hyperset-v0 \
    FOLIO_ACTOR=hyperset \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
EXPOSE 8000 8001
CMD ["python", "-m", "folio_lattice.server", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
