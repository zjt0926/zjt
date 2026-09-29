"""Class-scoped knowledge base retrieval with three modes (keyword / vector / hybrid).

The viewer's ``class_id`` is always taken from the session; both the
vector store and the FTS query enforce it. Excerpts come from the
SQLite chunks table, not the vector store (per knowledge-retrieval spec).
"""

import re

import jieba
from flask import Blueprint, jsonify, render_template, request, session
from markupsafe import Markup, escape

from . import vectorstore
from .auth import login_required
from .db import get_chunk, keyword_search, substring_search

bp = Blueprint("search", __name__)

RESULT_LIMIT = 10
VALID_MODES = {"keyword", "vector", "hybrid"}
DEFAULT_MODE = "hybrid"

NO_MATCH_MSG = "资料中未找到相关内容"


def highlight_snippet(snippet: str, query: str) -> Markup:
    """Return HTML-escaped snippet with query terms wrapped in <mark>."""
    safe = str(escape(snippet))
    terms = sorted(
        {t for t in jieba.cut(query) if len(t.strip()) >= 2},
        key=len,
        reverse=True,
    )
    if not terms:
        return Markup(safe)
    pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    return Markup(pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe))


def _enrich(hits, class_id, db_path=None):
    """Fill in title, char range, and snippet from the DB chunks table."""
    enriched = []
    for hit in hits:
        chunk = get_chunk(
            hit["material_id"], hit["chunk_index"], class_id=class_id, db_path=db_path
        )
        if chunk is None:
            continue
        enriched.append(
            {
                "material_id": hit["material_id"],
                "title": chunk["title"],
                "chunk_index": hit["chunk_index"],
                "char_start": chunk["char_start"],
                "char_end": chunk["char_end"],
                "snippet": chunk["body_text"],
                "score": hit.get("score", 0.0),
            }
        )
    return enriched


def _rrf_fuse(vector_hits, keyword_hits, k=60):
    """Reciprocal Rank Fusion: merge two ranked lists by position.

    Hits present in both lists get higher combined scores.
    """
    scores: dict[tuple[int, int], float] = {}
    for rank, h in enumerate(vector_hits, 1):
        key = (h["material_id"], h["chunk_index"])
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    for rank, h in enumerate(keyword_hits, 1):
        key = (h["material_id"], h["chunk_index"])
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)

    # Sort by fused score descending.
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    # Preserve original hit data for enrichment.
    hit_map = {}
    for h in vector_hits + keyword_hits:
        hit_map[(h["material_id"], h["chunk_index"])] = h
    return [
        {**hit_map[key], "score": round(fused, 4)}
        for key, fused in ranked
    ]


def _keyword_hits(class_id, query, k, db_path):
    """FTS keyword hits merged with substring hits (deduped, FTS first).

    The substring pass catches queries that are fragments of untokenizable
    strings (e.g. "LAT" inside an access key), which FTS5 can never match.
    """
    fts_hits = keyword_search(class_id, query, k=k, db_path=db_path)
    sub_hits = substring_search(class_id, query, k=k, db_path=db_path)
    seen = {(h["material_id"], h["chunk_index"]) for h in fts_hits}
    merged = list(fts_hits)
    merged.extend(
        h for h in sub_hits if (h["material_id"], h["chunk_index"]) not in seen
    )
    return merged[:k]


def _query_terms(query: str) -> set[str]:
    """Jieba tokens of the query (len>=2, lowercased) for overlap checks."""
    return {t.strip().lower() for t in jieba.cut(query) if len(t.strip()) >= 2}


def _has_token_overlap(hit, terms, class_id, db_path) -> bool:
    """True if the hit's chunk text shares at least one token with the query.

    Offline hash embeddings give spuriously high scores to very short
    queries (e.g. "LTA" vs a calculus chunk scored 0.29 from pure hash
    collision while sharing ZERO tokens). Requiring literal token overlap
    filters that noise out of the hybrid path.
    """
    chunk = get_chunk(
        hit["material_id"], hit["chunk_index"], class_id=class_id, db_path=db_path
    )
    if chunk is None:
        return False
    chunk_tokens = {
        t.strip().lower() for t in jieba.cut(chunk["body_text"]) if len(t.strip()) >= 2
    }
    return bool(terms & chunk_tokens)


def do_search(class_id, query, mode, k=RESULT_LIMIT, db_path=None, vector_path=None):
    """Dispatch to the selected search mode and return enriched hits."""
    from flask import current_app
    if db_path is None:
        db_path = current_app.config["DATABASE"]
    if vector_path is None:
        vector_path = current_app.config["VECTOR_DB_PATH"]

    if mode == "keyword":
        raw_hits = _keyword_hits(class_id, query, k, db_path)
        return _enrich(raw_hits, class_id, db_path)

    if mode == "vector":
        raw_hits = vectorstore.search(class_id, query, k=k, path=vector_path)
        return _enrich(raw_hits, class_id, db_path)

    # hybrid: fuse keyword(+substring) with vector hits by RRF. Vector hits
    # must share a literal token with the query, otherwise hash-collision
    # noise (zero-overlap chunks with spurious scores) would pollute results.
    v_hits = vectorstore.search(class_id, query, k=k, path=vector_path)
    terms = _query_terms(query)
    if terms:
        v_hits = [h for h in v_hits if _has_token_overlap(h, terms, class_id, db_path)]
    else:
        v_hits = []
    kw_hits = _keyword_hits(class_id, query, k, db_path)
    fused = _rrf_fuse(v_hits, kw_hits)
    return _enrich(fused[:k], class_id, db_path)


@bp.route("/api/knowledge/search", methods=["GET"])
@login_required(api=True)
def api_knowledge_search():
    """JSON search with mode selection (keyword / vector / hybrid)."""
    query = (request.args.get("q") or "").strip()
    mode = request.args.get("mode", DEFAULT_MODE).strip()
    if mode not in VALID_MODES:
        mode = DEFAULT_MODE
    if not query:
        return {"error": "missing query parameter 'q'", "hits": []}, 400

    hits = do_search(session["class_id"], query, mode)
    if not hits:
        return jsonify(
            {
                "class_id": session["class_id"],
                "query": query,
                "mode": mode,
                "message": NO_MATCH_MSG,
                "hits": [],
            }
        )
    return jsonify(
        {
            "class_id": session["class_id"],
            "query": query,
            "mode": mode,
            "hits": hits,
        }
    )


@bp.route("/search", methods=["GET"])
@login_required(api=False)
def search_view():
    """Render the knowledge search page with mode selector and traced hits."""
    query = (request.args.get("q") or "").strip()
    mode = request.args.get("mode", DEFAULT_MODE).strip()
    if mode not in VALID_MODES:
        mode = DEFAULT_MODE

    if query:
        hits = do_search(session["class_id"], query, mode)
    else:
        hits = []

    rendered = [
        {**hit, "snippet_html": highlight_snippet(hit["snippet"], query)}
        for hit in hits
    ]
    return render_template(
        "search.html",
        q=query,
        mode=mode,
        results=rendered,
        class_id=session["class_id"],
        no_match_msg=NO_MATCH_MSG if query and not hits else None,
    )
