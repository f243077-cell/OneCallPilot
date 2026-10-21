"""``POST /ingest/alert`` (API-003; FR-004, FR-005, FR-007; architecture §5.1).

The ``X-Ingest-Token`` is checked first, in constant time, before the body is
even parsed, so an unauthenticated caller learns nothing about the schema.
After the transaction commits: one agent job (unless ``AGENT_ENABLED=false``,
the manual baseline) and exactly one event (``incident.opened``, or
``evidence.added`` when a deduplicated alert brought a new signal type).
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from oncallpilot_contracts.api import AlertIn, IngestAccepted
from oncallpilot_contracts.ws import cut_evidence_payload
from pydantic import ValidationError
from redis.exceptions import RedisError

from oncallpilot.api.deps import get_services
from oncallpilot.api.errors import UnauthorizedError, ValidationFailedError
from oncallpilot.db.ingest import ingest_alert, threshold_overrides
from oncallpilot.detector.config import config_version

router = APIRouter(tags=["ingest"])
log = logging.getLogger(__name__)


@router.post("/ingest/alert", status_code=202, response_model=IngestAccepted)
async def post_alert(request: Request) -> JSONResponse:
    services = get_services(request)
    expected = services.ingest.token.get_secret_value().encode()
    given = request.headers.get("x-ingest-token", "").encode()
    if not expected or not hmac.compare_digest(given, expected):
        raise UnauthorizedError("missing or invalid ingest token")
    try:
        alert = AlertIn.model_validate_json(await request.body())
    except ValidationError as exc:
        errors = [
            {"loc": ["body", *(str(p) for p in e["loc"])], "msg": e["msg"]} for e in exc.errors()
        ]
        raise ValidationFailedError("invalid alert", {"errors": errors}) from exc

    try:
        bench_run = await services.queues.current_bench_run()
    except RedisError as exc:
        log.warning("bench run unknown (redis): %s", type(exc).__name__)
        bench_run = None

    async with services.pool.acquire() as conn:
        overrides = await threshold_overrides(conn)
        outcome = await ingest_alert(
            conn,
            alert,
            now=services.clock(),
            detector_config_version=config_version(services.ingest.detector_config, overrides),
            benchmark_run_id=bench_run,
        )

    extra = {"incident_id": outcome.incident_id}
    try:
        if outcome.opened is not None:
            if services.ingest.agent_enabled:
                await services.queues.enqueue_agent_job(outcome.incident_id)
            await services.events.publish(
                "incident.opened",
                outcome.incident_id,
                outcome.state_version,
                outcome.opened.opened_at,
                outcome.opened,
            )
            log.info("incident opened", extra=extra)
        elif outcome.added is not None:
            data, truncated = cut_evidence_payload(outcome.added.kind, outcome.added.payload)
            await services.events.publish(
                "evidence.added",
                outcome.incident_id,
                outcome.state_version,
                outcome.added.created_at,
                {
                    **outcome.added.model_dump(mode="json"),
                    "payload": data,
                    "payload_truncated": truncated,
                },
            )
    except RedisError as exc:
        # The incident is committed; the app still sees it through GET /incidents.
        log.error("post-commit publish failed: %s", type(exc).__name__, extra=extra)

    body = IngestAccepted(incident_id=outcome.incident_id, deduplicated=outcome.deduplicated)
    return JSONResponse(body.model_dump(mode="json"), status_code=202)
