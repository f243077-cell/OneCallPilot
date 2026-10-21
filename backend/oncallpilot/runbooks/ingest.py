"""Ingest ``runbooks/*.md`` into ``ocp.runbook_chunks`` (AI-026, AI-027; ADR-14).

Each file is split at its ``#``/``##``/``###`` headings, and long sections
again at paragraph breaks, into chunks of at most ~2,000 characters (about
500 tokens). Each chunk keeps its nearest heading. A chunk whose text did not
change (same ``content_hash``) is not re-embedded. Chunks are embedded with
``RETRIEVAL_DOCUMENT``.

The worker calls ``ingest_dir`` at start-up (B2.4) with the real embedding
provider; tests use ``FakeEmbeddingProvider``.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from oncallpilot.agent.providers.embeddings import EmbeddingProvider
from oncallpilot.db.pool import DbConn
from oncallpilot.db.runbooks import ChunkRow, existing_hashes, mark_stale, upsert_chunk

log = logging.getLogger(__name__)

MAX_CHUNK_CHARS = 2000
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


def split_markdown(source: str, text: str) -> list[ChunkRow]:
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            sections.append((match.group(2), [line]))
        else:
            sections[-1][1].append(line)
    chunks: list[ChunkRow] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        for piece in _pieces(body):
            content_hash = hashlib.sha256(piece.encode()).hexdigest()
            chunks.append(ChunkRow(source, len(chunks), heading, piece, content_hash))
    return chunks


def _pieces(body: str) -> list[str]:
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]
    pieces: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", body):
        while len(paragraph) > MAX_CHUNK_CHARS:  # one huge paragraph: hard cut
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:MAX_CHUNK_CHARS])
            paragraph = paragraph[MAX_CHUNK_CHARS:]
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > MAX_CHUNK_CHARS:
            pieces.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        pieces.append(current)
    return [p.strip() for p in pieces if p.strip()]


async def ingest_file(
    conn: DbConn, embedder: EmbeddingProvider, path: Path, source: str
) -> tuple[int, int]:
    """Returns (chunks embedded, chunks unchanged)."""
    chunks = split_markdown(source, path.read_text(encoding="utf-8"))
    known = await existing_hashes(conn, source)
    changed = [c for c in chunks if known.get(c.chunk_index) != c.content_hash]
    if changed:
        vectors = await embedder.embed([c.content for c in changed], "RETRIEVAL_DOCUMENT")
        async with conn.transaction():
            for chunk, vector in zip(changed, vectors, strict=True):
                await upsert_chunk(conn, chunk, vector, embedder.model)
    stale = await mark_stale(conn, source, len(chunks))
    if stale:
        log.info("%s: %d old chunks marked stale", source, stale)
    return len(changed), len(chunks) - len(changed)


async def ingest_dir(conn: DbConn, embedder: EmbeddingProvider, root: Path) -> dict[str, int]:
    embedded = 0
    unchanged = 0
    for path in sorted(root.glob("*.md")):
        done, same = await ingest_file(conn, embedder, path, f"runbooks/{path.name}")
        embedded += done
        unchanged += same
    return {"embedded": embedded, "unchanged": unchanged}
