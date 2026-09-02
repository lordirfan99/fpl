FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY api/app ./app
# Snapshot fallback baked from the repo-root data/ (gitignored; supplied by the
# build context). GCS (FPL_SNAPSHOT_BUCKET) remains the primary store.
COPY data ./data
ENV FPL_DATA_DIR=/app/data

USER 65532:65532
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
