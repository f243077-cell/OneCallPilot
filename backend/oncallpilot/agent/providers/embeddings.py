"""The ``EmbeddingProvider`` interface (ADR-14) and a deterministic fake.

Vectors are 768 floats, L2-normalised in code (the API normalises only the
full 3072-dimension output). Runbook chunks use ``RETRIEVAL_DOCUMENT`` and
search queries ``RETRIEVAL_QUERY``.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Literal, Protocol

DIMENSIONS = 768
TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise ValueError("cannot normalise a zero vector")
    return [v / norm for v in vector]


class EmbeddingProvider(Protocol):
    name: str
    model: str

    async def embed(self, texts: list[str], task: TaskType) -> list[list[float]]: ...


class FakeEmbeddingProvider:
    """Hashed bag of words: texts that share words are similar. For tests and CI."""

    name = "fake"
    model = "fake-embedding"

    async def embed(self, texts: list[str], task: TaskType) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * DIMENSIONS
            for word in re.findall(r"[a-z0-9_]+", text.lower()):
                digest = hashlib.sha256(word.encode()).digest()
                vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0
            if not any(vector):
                vector[0] = 1.0
            vectors.append(normalise(vector))
        return vectors
