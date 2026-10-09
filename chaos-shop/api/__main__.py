"""Entry point: ``python -m api`` (cs-api-* containers, port 8000)."""

import os

from fastapi import FastAPI

from api.app import create_app
from api.deps import Deps
from api.metrics import ApiMetrics
from api.payments_client import HttpPayments
from api.pg import PgStore
from api.rediscache import RedisCache
from common.identity import Identity, require_env
from common.serve import serve

PORT = 8000


def build(identity: Identity) -> FastAPI:
    metrics = ApiMetrics(identity)
    deps = Deps(
        store=PgStore(require_env("CS_DATABASE_URL"), metrics),
        cache=RedisCache(require_env("CS_REDIS_URL")),
        payments=HttpPayments(require_env("CS_PAYMENTS_URL")),
    )
    return create_app(
        identity,
        deps,
        metrics,
        runner_admin_token=os.environ.get("RUNNER_ADMIN_TOKEN", ""),
    )


def main() -> None:
    serve("api", PORT, build, with_commit=True)


if __name__ == "__main__":
    main()
