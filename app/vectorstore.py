"""ChromaDB-backed vector store for class-scoped knowledge retrieval.

Embeddings are computed fully offline: jieba word tokens (plus token
bigrams) are projected into a fixed-size vector via the feature-hashing
trick and L2-normalized, so Chroma's cosine distance is directly
meaningful. No model download is required at build/run time.

Class isolation is enforced at query time with a Chroma metadata filter
(``where={"class_id": ...}``); the class id always comes from the
viewer's session, never from request data.

Content tracing: every indexed chunk stores its source ``material_id``,
``title`` and ``chunk_index`` as metadata, so every search hit can point
back to the exact material and passage it came from.
"""

import hashlib
import math
import os
from functools import lru_cache
from typing import Optional

# Must be set before importing chromadb to disable its telemetry pings.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
import jieba

from .knowledge import chunk_text

COLLECTION_NAME = "knowledge_chunks"
EMBED_DIM = 1024
# Cosine-similarity floor. The spec suggests 0.35 for ML embeddings;
# our offline hashing embeddings have lower absolute scores, so we use
# a lower floor that still filters zero-overlap noise (score 0.0).
MIN_SIMILARITY = 0.05


class OfflineEmbeddingFunction(EmbeddingFunction[Documents]):
    """Deterministic Chinese-friendly embedding (feature hashing)."""

    def __call__(self, input: Documents) -> Embeddings:
        return [embed_text(text) for text in input]


def _features(text: str) -> list[str]:
    """Tokenize with jieba; emit unigrams + adjacent bigrams."""
    tokens = [t.strip() for t in jieba.cut(text) if t.strip()]
    features = list(tokens)
    features.extend(a + b for a, b in zip(tokens, tokens[1:]))
    return features


def embed_text(text: str) -> list[float]:
    """Hash features into an L2-normalized ``EMBED_DIM`` vector."""
    vec = [0.0] * EMBED_DIM
    for feat in _features(text or ""):
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % EMBED_DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


@lru_cache(maxsize=4)
def _get_collection(path: str):
    os.makedirs(path, exist_ok=True)
    client = chromadb.PersistentClient(path=path)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=OfflineEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )


def _collection(path: Optional[str] = None):
    if path is None:
        from flask import current_app

        path = current_app.config["VECTOR_DB_PATH"]
    return _get_collection(path)


def index_material(
    material_id: int,
    class_id: int,
    title: str,
    body_text: str,
    path: Optional[str] = None,
) -> int:
    """Chunk and (re)index one material. Returns the number of chunks.

    Idempotent: existing chunks for the same ``material_id`` are removed
    first, so re-indexing after an update never leaves stale vectors.
    """
    col = _collection(path)
    col.delete(where={"material_id": int(material_id)})

    chunks = chunk_text(body_text or "")
    if not chunks:
        return 0

    ids = [f"{material_id}-{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "material_id": int(material_id),
            "class_id": int(class_id),
            "chunk_index": i,
            "title": str(title),
        }
        for i in range(len(chunks))
    ]
    col.upsert(
        ids=ids,
        embeddings=[embed_text(c["text"]) for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=metadatas,
    )
    return len(chunks)


def remove_material(material_id: int, path: Optional[str] = None) -> None:
    """Delete all chunks belonging to a material."""
    _collection(path).delete(where={"material_id": int(material_id)})


def search(
    class_id: int,
    query: str,
    k: int = 5,
    path: Optional[str] = None,
) -> list[dict]:
    """Search within ONE class only; return ranked hits.

    Each hit: ``{material_id, chunk_index, score}`` — the caller
    enriches with title/char_range/snippet from the DB (per spec:
    excerpt must come from the DB, not the vector store).
    """
    query = (query or "").strip()
    if not query:
        return []

    col = _collection(path)
    result = col.query(
        query_embeddings=[embed_text(query)],
        n_results=k,
        where={"class_id": int(class_id)},
        include=["metadatas", "distances"],
    )
    if not result.get("ids") or not result["ids"][0]:
        return []

    hits: list[dict] = []
    for meta, dist in zip(
        result["metadatas"][0],
        result["distances"][0],
    ):
        score = round(max(0.0, 1.0 - float(dist)), 4)
        if score < MIN_SIMILARITY:
            continue
        hits.append(
            {
                "material_id": meta["material_id"],
                "chunk_index": meta["chunk_index"],
                "score": score,
            }
        )
    return hits


def reindex_all(db_path: str, vector_path: str) -> tuple[int, int]:
    """Rebuild the vector index from every knowledge entry.

    Returns ``(materials_indexed, chunks_indexed)``.
    """
    from .db import get_conn, insert_chunks

    conn = get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT ke.material_id AS material_id, ke.class_id AS class_id, "
            "m.title AS title, ke.body_text AS body_text "
            "FROM knowledge_entries ke "
            "JOIN materials m ON m.id = ke.material_id"
        ).fetchall()
    finally:
        conn.close()

    n_materials = 0
    n_chunks = 0
    for row in rows:
        body = row["body_text"] or ""
        chunks = chunk_text(body)
        # Write chunks to DB (for keyword search + excerpt tracing).
        insert_chunks(row["material_id"], row["class_id"], chunks, db_path)
        # Write vectors to ChromaDB.
        n = index_material(
            material_id=row["material_id"],
            class_id=row["class_id"],
            title=row["title"],
            body_text=body,
            path=vector_path,
        )
        n_materials += 1
        n_chunks += n
    return n_materials, n_chunks
