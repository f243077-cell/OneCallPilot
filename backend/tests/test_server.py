"""backend-api runs under uvicorn (Dockerfile CMD): it must be able to upgrade
WebSocket connections, or /ws/incidents refuses every client (API-014).

The gateway tests use Starlette's TestClient, which needs no WebSocket
library, so only this test catches a missing one.
"""

from typing import Any

import uvicorn


async def _app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
    return None


def test_uvicorn_has_a_websocket_implementation() -> None:
    config = uvicorn.Config(_app, ws="auto", lifespan="off")
    config.load()
    assert config.ws_protocol_class is not None
