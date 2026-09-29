"""Rebuild the ChromaDB vector index from all knowledge_entries.

Run directly (reads DATABASE_PATH / VECTOR_DB_PATH env vars, with
project-local defaults)::

    python scripts/reindex_vectors.py [db_path]

Idempotent: materials are upserted (their old chunks replaced), so the
script is safe to run on every container start.
"""

import os
import sys

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

DEFAULT_DB = os.environ.get(
    "DATABASE_PATH", os.path.join(_PROJECT_ROOT, "data", "app.db")
)
DEFAULT_VECTOR = os.environ.get(
    "VECTOR_DB_PATH", os.path.join(_PROJECT_ROOT, "data", "chroma")
)


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    vector_path = DEFAULT_VECTOR

    from app.vectorstore import reindex_all

    n_materials, n_chunks = reindex_all(db_path, vector_path)
    print(
        f"[reindex] indexed {n_materials} material(s), "
        f"{n_chunks} chunk(s) into {vector_path}"
    )


if __name__ == "__main__":
    main()
