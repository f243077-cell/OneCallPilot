"""Entry point: ``python -m payments`` (cs-payments container, port 8002)."""

import os

from fastapi import FastAPI

from common.identity import Identity
from common.serve import serve
from payments.app import create_app

PORT = 8002


def build(identity: Identity) -> FastAPI:
    return create_app(identity, chaos_token=os.environ.get("CHAOS_TOKEN", ""))


def main() -> None:
    serve("payments", PORT, build, with_commit=False)


if __name__ == "__main__":
    main()
