"""File parsing and chunking for knowledge base ingestion (MVP: txt/md only)."""

import re

ALLOWED_EXTENSIONS = {".txt", ".md"}

# Chunking parameters — per knowledge-retrieval spec: ~800 char window.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def parse_file(file_path: str, ext: str) -> str:
    """Read a text file and return its body as a string.

    Supports ``.txt`` and ``.md``. Raises ``ValueError`` for unsupported
    extensions or files that cannot be decoded as UTF-8, so the caller
    can roll back the upload transaction.
    """
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"unsupported extension: {ext}")
    with open(file_path, "rb") as f:
        raw = f.read()
    if not raw:
        raise ValueError("empty file")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid utf-8: {exc}") from exc


def chunk_text(
    text: str,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict]:
    """Split *text* into overlapping chunks with character ranges.

    Returns a list of ``{"text": str, "char_start": int, "char_end": int}``
    where ``char_start`` / ``char_end`` are offsets into the original
    *text* (0-based, half-open). The offsets are used for source tracing.
    """
    if not text or not text.strip():
        return []

    paragraphs = list(re.finditer(r"[^\n]+", text))
    if not paragraphs:
        return []

    # Group paragraphs into windows of ~``size`` characters.
    windows: list[tuple[int, int]] = []  # (start, end) offsets in original text
    cur_start = paragraphs[0].start()
    cur_end = paragraphs[0].end()
    for m in paragraphs[1:]:
        if m.end() - cur_start <= size:
            cur_end = m.end()
        else:
            windows.append((cur_start, cur_end))
            # Overlap: start the next window a bit before the current end.
            overlap_start = max(m.start(), cur_end - overlap)
            cur_start = overlap_start
            cur_end = m.end()
    windows.append((cur_start, cur_end))

    # Hard-split any window still longer than ``size``.
    expanded: list[tuple[int, int]] = []
    for s, e in windows:
        pos = s
        while e - pos > size:
            expanded.append((pos, pos + size))
            pos = pos + size - overlap
        if pos < e:
            expanded.append((pos, e))

    return [
        {"text": text[s:e], "char_start": s, "char_end": e}
        for s, e in expanded
    ]
