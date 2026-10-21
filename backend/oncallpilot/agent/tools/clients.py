"""HTTP clients for Prometheus and Loki (read-only query APIs only).

Every call has an explicit timeout (``CLAUDE.md`` §4.2). Only ``GET`` on the
query endpoints is ever sent; the queries themselves come from the fixed
templates in ``promql.py`` and ``logql.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

QUERY_TIMEOUT_SECONDS = 10.0


class QueryError(Exception):
    """The backend answered with an error or something unreadable."""


def _unix(at: datetime) -> str:
    return f"{at.timestamp():.3f}"


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> Any:
    try:
        response = await client.get(url, params=params, timeout=QUERY_TIMEOUT_SECONDS)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise QueryError(type(exc).__name__) from exc
    if not isinstance(body, dict) or body.get("status") != "success":
        raise QueryError("query failed")
    return body.get("data")


class PrometheusClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")

    async def query_range(
        self, promql: str, start: datetime, end: datetime, step_seconds: int
    ) -> list[tuple[float, float]]:
        """(unix seconds, value) of the first series; NaN and Inf points are dropped."""
        data = await _get(
            self._client,
            f"{self._base}/api/v1/query_range",
            {"query": promql, "start": _unix(start), "end": _unix(end), "step": f"{step_seconds}s"},
        )
        result = (data or {}).get("result") or []
        if not result:
            return []
        points: list[tuple[float, float]] = []
        for ts, raw in result[0].get("values", []):
            value = float(raw)
            if value == value and value not in (float("inf"), float("-inf")):
                points.append((float(ts), value))
        return points


class LokiClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")

    async def query_range(
        self, logql: str, start: datetime, end: datetime, limit: int
    ) -> list[tuple[dict[str, str], int, str]]:
        """Log lines, newest first: (stream labels, unix nanoseconds, line)."""
        data = await _get(
            self._client,
            f"{self._base}/loki/api/v1/query_range",
            {
                "query": logql,
                "start": str(int(start.timestamp() * 1e9)),
                "end": str(int(end.timestamp() * 1e9)),
                "limit": str(limit),
                "direction": "backward",
            },
        )
        lines: list[tuple[dict[str, str], int, str]] = []
        for stream in (data or {}).get("result") or []:
            labels = stream.get("stream") or {}
            for ns, line in stream.get("values") or []:
                lines.append((labels, int(ns), str(line)))
        lines.sort(key=lambda item: item[1], reverse=True)
        return lines[:limit]

    async def query_vector(self, logql: str, at: datetime) -> list[tuple[dict[str, str], float]]:
        """An instant metric query: (labels, value) per series."""
        data = await _get(
            self._client,
            f"{self._base}/loki/api/v1/query",
            {"query": logql, "time": str(int(at.timestamp() * 1e9))},
        )
        series: list[tuple[dict[str, str], float]] = []
        for item in (data or {}).get("result") or []:
            series.append((item.get("metric") or {}, float(item["value"][1])))
        return series
