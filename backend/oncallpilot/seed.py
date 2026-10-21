"""``python -m oncallpilot.seed <timeline.json>``: the engine of ``ocp seed-incident`` (B1.4).

Writes a fixture timeline's ``final`` incident into the database and
publishes the timeline's events to ``ocp:events`` in order, so the app (and
the gateway tests) see a real incident without the detector or the agent
(phases.md §4). It reads ``DATABASE_URL`` and ``REDIS_URL`` like the backend.

- ``--new-ids`` replaces every incident, evidence, hypothesis, proposal and
  execution UUID with a fresh one, so the same timeline can be seeded again.
- ``--realtime`` waits each step's ``delay_ms`` before publishing it, as the
  app's mock mode does; by default events are published back to back.

Never used by the agent or the API. Seeding writes ``audit_log`` rows with
actor ``system`` and ``"seeded": true``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

from oncallpilot_contracts.timeline import Timeline
from redis.asyncio import Redis

from oncallpilot.db.pool import create_pool
from oncallpilot.db.seed import write_incident_detail
from oncallpilot.events import EventStream

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def with_new_ids(timeline: Timeline) -> Timeline:
    """The same timeline with every id of the incident's own rows replaced."""
    final = timeline.final
    own = {str(final.id)}
    own |= {str(e.id) for e in final.evidence}
    own |= {str(h.id) for h in final.hypotheses}
    own |= {str(p.id) for p in final.proposals}
    own |= {str(x.id) for x in final.executions}
    mapping = {old: str(uuid.uuid4()) for old in own}
    text = timeline.model_dump_json()
    replaced = _UUID.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)
    return Timeline.model_validate_json(replaced)


async def seed(timeline: Timeline, database_url: str, redis_url: str, realtime: bool) -> list[str]:
    pool = await create_pool(database_url, max_size=1)
    try:
        async with pool.acquire() as conn:
            await write_incident_detail(conn, timeline.final)
    finally:
        await pool.close()
    event_ids: list[str] = []
    async with Redis.from_url(redis_url, decode_responses=True, socket_timeout=5) as redis:
        events = EventStream(redis)
        for step in timeline.steps:
            if realtime and step.delay_ms:
                await asyncio.sleep(step.delay_ms / 1000)
            event = step.event
            event_ids.append(
                await events.publish(
                    event.event, event.incident_id, event.state_version, event.ts, event.data
                )
            )
    return event_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m oncallpilot.seed", description=__doc__)
    parser.add_argument("timeline", type=Path, help="a contracts/fixtures/timelines/*.json file")
    parser.add_argument("--new-ids", action="store_true", help="seed under fresh UUIDs")
    parser.add_argument("--realtime", action="store_true", help="honour each step's delay_ms")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    redis_url = os.environ.get("REDIS_URL")
    if not database_url or not redis_url:
        print("seed: DATABASE_URL and REDIS_URL must be set", file=sys.stderr)
        return 2
    timeline = Timeline.model_validate_json(args.timeline.read_text(encoding="utf-8"))
    if args.new_ids:
        timeline = with_new_ids(timeline)
    event_ids = asyncio.run(seed(timeline, database_url, redis_url, args.realtime))
    print(
        json.dumps(
            {
                "incident_id": str(timeline.final.id),
                "timeline": timeline.name,
                "events": len(event_ids),
                "last_event_id": event_ids[-1] if event_ids else None,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
