"""Runbook chunks in pgvector (DB-009; AI-009, AI-026, AI-027).

``ocp_backend`` may not DELETE (DB-011), so a chunk that no longer exists
after re-ingestion is blanked and marked ``content_hash = 'stale'``; search
skips those rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from oncallpilot.db.pool import DbConn

STALE = "stale"


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


@dataclass(frozen=True)
class ChunkRow:
    source: str
    chunk_index: int
    heading: str | None
    content: str
    content_hash: str


async def existing_hashes(conn: DbConn, source: str) -> dict[int, str]:
    rows = await conn.fetch(
        "select chunk_index, content_hash from ocp.runbook_chunks where source = $1", source
    )
    return {r["chunk_index"]: r["content_hash"] for r in rows}


async def upsert_chunk(
    conn: DbConn, chunk: ChunkRow, embedding: list[float], embedding_model: str
) -> None:
    await conn.execute(
        "insert into ocp.runbook_chunks (source, chunk_index, heading, content, content_hash,"
        " embedding, embedding_model) values ($1, $2, $3, $4, $5, $6::vector, $7)"
        " on conflict (source, chunk_index) do update set heading = excluded.heading,"
        " content = excluded.content, content_hash = excluded.content_hash,"
        " embedding = excluded.embedding, embedding_model = excluded.embedding_model",
        chunk.source,
        chunk.chunk_index,
        chunk.heading,
        chunk.content,
        chunk.content_hash,
        vector_literal(embedding),
        embedding_model,
    )


async def mark_stale(conn: DbConn, source: str, from_index: int) -> int:
    result = await conn.execute(
        "update ocp.runbook_chunks set content = '', heading = null, content_hash = $3"
        " where source = $1 and chunk_index >= $2 and content_hash <> $3",
        source,
        from_index,
        STALE,
    )
    return int(result.split()[-1])


async def search(
    conn: DbConn, query_embedding: list[float], k: int
) -> list[tuple[str, str | None, str, float]]:
    """(source, heading, content, cosine similarity), most similar first."""
    rows = await conn.fetch(
        "select source, heading, content, 1 - (embedding <=> $1::vector) as similarity"
        " from ocp.runbook_chunks where content_hash <> $3"
        " order by embedding <=> $1::vector limit $2",
        vector_literal(query_embedding),
        k,
        STALE,
    )
    return [(r["source"], r["heading"], r["content"], float(r["similarity"])) for r in rows]
