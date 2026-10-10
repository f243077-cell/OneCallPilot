"""cs-api metrics (contracts/telemetry.md §2.3, §2.4)."""

from collections.abc import Callable, Iterable

from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.metrics_core import Metric
from prometheus_client.registry import Collector

from common.identity import Identity
from common.metrics import DB_POOL_WAIT_BUCKETS, HTTP_BUCKETS, UPSTREAM_BUCKETS, Telemetry

# Route templates counted in http_* metrics; anything else is `other`.
# /metrics and /internal/* are not counted at all.
ROUTES = frozenset({"/products", "/cart", "/checkout", "/orders", "/healthz"})
METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})


def route_label(path: str) -> str:
    return path if path in ROUTES else "other"


def method_label(method: str) -> str:
    upper = method.upper()
    return upper if upper in METHODS else "OTHER"


def is_counted(path: str) -> bool:
    return path != "/metrics" and not path.startswith("/internal/") and path != "/internal"


class ApiMetrics:
    def __init__(self, identity: Identity, *, with_process: bool = True) -> None:
        self.telemetry = Telemetry(identity, with_process=with_process)
        registry = self.telemetry.registry
        self.http_requests = Counter(
            "http_requests_total",
            "Completed HTTP requests",
            ["route", "method", "status"],
            registry=registry,
        )
        self.http_duration = Histogram(
            "http_request_duration_seconds",
            "Request latency",
            ["route", "method"],
            buckets=HTTP_BUCKETS,
            registry=registry,
        )
        self.db_pool_wait = Histogram(
            "db_pool_wait_seconds",
            "Time spent waiting to acquire a pool connection",
            buckets=DB_POOL_WAIT_BUCKETS,
            registry=registry,
        )
        self.db_pool_timeouts = Counter(
            "db_pool_timeouts_total",
            "Pool acquisitions that timed out",
            registry=registry,
        )
        self.cache_operations = Counter(
            "cache_operations_total",
            "Cache reads against cs-redis",
            ["cache", "result"],
            registry=registry,
        )
        self.upstream_duration = Histogram(
            "upstream_request_duration_seconds",
            "Latency of calls to upstream dependencies",
            ["upstream"],
            buckets=UPSTREAM_BUCKETS,
            registry=registry,
        )

    def watch_pool(self, pool_counts: Callable[[], tuple[int, int]]) -> None:
        """Report `db_pool_connections{state}` from the store's pool at scrape time."""
        self.telemetry.registry.register(_PoolCollector(pool_counts))

    def render(self) -> bytes:
        return self.telemetry.render()


class _PoolCollector(Collector):
    def __init__(self, pool_counts: Callable[[], tuple[int, int]]) -> None:
        self._pool_counts = pool_counts

    def collect(self) -> Iterable[Metric]:
        in_use, idle = self._pool_counts()
        family = GaugeMetricFamily(
            "db_pool_connections",
            "Store-database pool connections by state",
            labels=["state"],
        )
        family.add_metric(["in_use"], in_use)
        family.add_metric(["idle"], idle)
        yield family
