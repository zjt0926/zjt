#!/bin/sh
set -e

# Initialize the database (tables + seed data) if it does not exist yet.
# The app factory also does this, but doing it here keeps startup explicit
# and avoids races between gunicorn workers on the very first boot.
if [ ! -f "${DATABASE_PATH:-/app/data/app.db}" ]; then
  echo "[entrypoint] Database not found, initializing..."
  python scripts/init_db.py "${DATABASE_PATH:-/app/data/app.db}"
fi

# Rebuild the ChromaDB knowledge index (idempotent; also backfills
# materials uploaded before the vector feature existed).
echo "[entrypoint] Rebuilding knowledge vector index..."
python scripts/reindex_vectors.py "${DATABASE_PATH:-/app/data/app.db}"

exec "$@"
