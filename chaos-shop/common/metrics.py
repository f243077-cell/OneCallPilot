"""C7 metrics: every series carries the base labels service, release and instance.

Metrics are defined without the base labels in an inner registry. The exposed
registry wraps it and adds the base labels to every sample, including the
default ``process_*`` collector (contracts/telemetry.md §2.2).
"""

from collections.abc import Iterable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    ProcessCollector,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.metrics_core import Metric
from prometheus_client.registry import Collector

from common.identity import Identity

# `*_created` series are not part of the contract.
disable_created_metrics()  # type: ignore[no-untyped-call]

CONTENT_TYPE = CONTENT_TYPE_LATEST

# Fixed bucket lists (contracts/telemetry.md §2.5).
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
UPSTREAM_BUCKETS = HTTP_BUCKETS
DB_POOL_WAIT_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30)
WORKER_JOB_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


class _BaseLabels(Collector):
    def __init__(self, inner: CollectorRegistry, labels: dict[str, str]) -> None:
        self._inner = inner
        self._labels = labels

    def collect(self) -> Iterable[Metric]:
        for family in self._inner.collect():
            labelled = Metric(family.name, family.documentation, family.type, family.unit)
            labelled.samples = [
                sample._replace(labels={**self._labels, **sample.labels})
                for sample in family.samples
            ]
            yield labelled


class Telemetry:
    """``registry`` is where a service defines its metrics; ``render()`` exposes them."""

    def __init__(self, identity: Identity, *, with_process: bool = True) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self._exposed = CollectorRegistry(auto_describe=False)
        self._exposed.register(
            _BaseLabels(
                self.registry,
                {
                    "service": identity.service,
                    "release": identity.release,
                    "instance": identity.instance,
                },
            )
        )
        if with_process:
            ProcessCollector(registry=self.registry)
        if identity.commit:
            app_info = Gauge("app_info", "Build information", ["commit"], registry=self.registry)
            app_info.labels(commit=identity.commit).set(1)

    def render(self) -> bytes:
        return generate_latest(self._exposed)
