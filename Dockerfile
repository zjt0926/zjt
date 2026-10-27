FROM python:3.13-slim

WORKDIR /app

# System deps: curl for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY run.py ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV DATABASE_PATH=/app/data/app.db \
    UPLOAD_FOLDER=/app/uploads \
    VECTOR_DB_PATH=/app/data/chroma

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
# Single process with a thread pool: ChromaDB's local PersistentClient
# keeps per-process reader caches, so multiple worker PROCESSES could
# serve stale vector state after another process writes. Threads share
# the process (state stays consistent) and let an in-flight SSE stream
# coexist with other requests (health checks, page loads).
CMD ["gunicorn", "-w", "1", "-k", "gthread", "--threads", "4", "-b", "0.0.0.0:8080", "run:app"]
