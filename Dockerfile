FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 folio \
    && mkdir -p /data \
    && chown -R folio:folio /app /data
USER folio

ENV FOLIO_DB_PATH=/data/folio.db \
    FOLIO_BLOB_ROOT=/data/blobs \
    FOLIO_TENANT_ID=hyperset-v0 \
    FOLIO_ACTOR=hyperset \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8000 8001
CMD ["python", "-m", "folio_lattice.server", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
