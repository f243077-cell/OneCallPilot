"""``search_runbooks`` (architecture §6.3; AI-009): pgvector cosine search, k ≤ 3.

The query is embedded with ``RETRIEVAL_QUERY``; each hit's content is cut to
1,500 characters. Runbook text is untrusted data like any tool output.
"""

from __future__ import annotations

from oncallpilot_contracts.domain import RunbookChunkHit, RunbookHitPayload

from oncallpilot.agent.providers.embeddings import EmbeddingProvider
from oncallpilot.agent.tools.params import SearchRunbooksParams
from oncallpilot.db.pool import DbConn
from oncallpilot.db.runbooks import search

MAX_CONTENT = 1500


async def search_runbooks(
    conn: DbConn, embedder: EmbeddingProvider, params: SearchRunbooksParams
) -> RunbookHitPayload:
    [vector] = await embedder.embed([params.query], "RETRIEVAL_QUERY")
    hits = await search(conn, vector, params.k)
    return RunbookHitPayload(
        chunks=[
            RunbookChunkHit(
                source=source,
                heading=heading[:200] if heading else None,
                content=content[:MAX_CONTENT],
                similarity=max(-1.0, min(1.0, similarity)),
            )
            for source, heading, content, similarity in hits
        ]
    )
