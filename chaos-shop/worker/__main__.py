"""Entry point: ``python -m worker`` (cs-worker-* containers, port 8001)."""

import os

from fastapi import FastAPI

from common.identity import Identity, require_env
from common.serve import serve
from worker.app import create_app
from worker.backends import PgWorkerStore, RedisJobSource
from worker.jobs import JobRunner, WorkerMetrics, parse_batch_size

PORT = 8001


def build(identity: Identity) -> FastAPI:
    batch_size = parse_batch_size(os.environ.get("QUEUE_BATCH_SIZE"))
    metrics = WorkerMetrics(identity)
    source = RedisJobSource(require_env("CS_REDIS_URL"))
    store = PgWorkerStore(require_env("CS_DATABASE_URL"))
    runner = JobRunner(source, store, metrics, batch_size=batch_size)
    return create_app(identity, runner, metrics, source, store)


def main() -> None:
    serve("worker", PORT, build, with_commit=True)


if __name__ == "__main__":
    main()
