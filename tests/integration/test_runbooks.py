"""Runbook ingestion and search_runbooks on pgvector (AI-009, AI-026, AI-027, DB-009)."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from oncallpilot.agent.providers.embeddings import FakeEmbeddingProvider, TaskType
from oncallpilot.agent.tools.params import SearchRunbooksParams
from oncallpilot.agent.tools.search_runbooks import search_runbooks
from oncallpilot.runbooks.ingest import ingest_dir, ingest_file


class CountingEmbedder(FakeEmbeddingProvider):
    def __init__(self) -> None:
        self.embedded = 0

    async def embed(self, texts: list[str], task: TaskType) -> list[list[float]]:
        self.embedded += len(texts)
        return await super().embed(texts, task)


@pytest.fixture
def books(tmp_path: Path) -> Path:
    (tmp_path / "rollback.md").write_text(
        "# Rolling back a deploy\n\nUse rollback_deploy to return api to the previous release.\n\n"
        "## When\n\nErrors start right after a deploy.\n",
        encoding="utf-8",
    )
    (tmp_path / "cache.md").write_text(
        "# Redis cache errors\n\n"
        "clear_cache flushes the pricing cache when it holds bad entries.\n",
        encoding="utf-8",
    )
    return tmp_path


async def test_ingest_then_search(pool: asyncpg.Pool, books: Path) -> None:
    embedder = CountingEmbedder()
    async with pool.acquire() as conn:
        counts = await ingest_dir(conn, embedder, books)
        assert counts == {"embedded": 3, "unchanged": 0}
        hits = await search_runbooks(
            conn, embedder, SearchRunbooksParams(query="rollback the deploy release", k=2)
        )
    assert len(hits.chunks) == 2
    assert hits.chunks[0].source == "runbooks/rollback.md"
    assert hits.chunks[0].similarity >= hits.chunks[1].similarity


async def test_unchanged_chunks_are_not_embedded_again(pool: asyncpg.Pool, books: Path) -> None:
    embedder = CountingEmbedder()
    async with pool.acquire() as conn:
        await ingest_dir(conn, embedder, books)
        first = embedder.embedded
        counts = await ingest_dir(conn, embedder, books)
    assert counts == {"embedded": 0, "unchanged": 3}
    assert embedder.embedded == first


async def test_removed_chunks_are_marked_stale_and_not_found(
    pool: asyncpg.Pool, books: Path
) -> None:
    embedder = CountingEmbedder()
    path = books / "rollback.md"
    async with pool.acquire() as conn:
        await ingest_file(conn, embedder, path, "runbooks/rollback.md")
        path.write_text("# Rolling back a deploy\n\nShorter now.\n", encoding="utf-8")
        await ingest_file(conn, embedder, path, "runbooks/rollback.md")
        rows = await conn.fetch(
            "select chunk_index, content_hash from ocp.runbook_chunks order by chunk_index"
        )
        hits = await search_runbooks(conn, embedder, SearchRunbooksParams(query="errors when", k=3))
    assert [r["content_hash"] == "stale" for r in rows] == [False, True]
    assert all(h.content for h in hits.chunks)


async def test_ingest_works_as_the_backend_role(pool: asyncpg.Pool, books: Path) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("set local role ocp_backend")
        counts = await ingest_dir(conn, CountingEmbedder(), books)
    assert counts["embedded"] == 3
