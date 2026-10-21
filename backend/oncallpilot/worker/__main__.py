"""backend-worker: ``python -m oncallpilot.worker`` (architecture §2.3, §5.2, §6.8).

One process with these loops:

- the agent-job consumer (``ocp:agent:jobs``, group ``agents``), which runs
  one ``Investigation`` per incident with ``DbEffects`` and the
  ``RunnerProposer`` (signed dry runs with ``RUNNER_LINK_KEY`` only);
- the detector, every ``DETECTOR_INTERVAL_SECONDS``, posting alerts to
  ``POST /ingest/alert``;
- the runner-result consumer, the signal verifier and the sweeper
  (``worker/loops.py``).

At start-up it ingests ``runbooks/`` (unchanged chunks are skipped). SIGTERM
stops both loops and waits for running investigations.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import signal
import socket
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from oncallpilot_contracts.signing import key_from_hex
from redis.asyncio import Redis
from redis.exceptions import RedisError

from oncallpilot.agent.effects import DbEffects
from oncallpilot.agent.prompts import FilePrompts
from oncallpilot.agent.proposer import RunnerProposer
from oncallpilot.agent.providers.base import LLMProvider, LLMUnavailableError
from oncallpilot.agent.providers.embeddings import EmbeddingProvider, FakeEmbeddingProvider
from oncallpilot.agent.providers.fake import FakeProvider
from oncallpilot.agent.providers.gemini import GeminiEmbeddingProvider, GeminiProvider
from oncallpilot.agent.run import Investigation
from oncallpilot.agent.state_machine import MAX_TOOL_CALLS
from oncallpilot.agent.toolbox import ToolBox
from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient
from oncallpilot.agent.tools.get_service_health import docker_base_url
from oncallpilot.catalogue import Catalogue
from oncallpilot.db.pool import create_pool
from oncallpilot.detector.config import load_config, read_config
from oncallpilot.detector.engine import Detector
from oncallpilot.detector.loop import DetectorLoop, HttpAlertSink
from oncallpilot.detector.sources import LiveSampleSource
from oncallpilot.events import EventStream
from oncallpilot.jsonlog import configure_logging
from oncallpilot.ledger import latest_reset, read_records
from oncallpilot.runbooks.ingest import ingest_dir
from oncallpilot.runner_link import RunnerLink
from oncallpilot.settings import WorkerSettings
from oncallpilot.worker.agent_jobs import AgentJobConsumer
from oncallpilot.worker.loops import (
    SWEEP_EVERY_SECONDS,
    LatestSamples,
    RunnerResultConsumer,
    SignalVerifier,
    Sweeper,
    every,
)
from oncallpilot.worker.outcomes import Outcomes

log = logging.getLogger("oncallpilot.worker")

# Above the consumer's XREADGROUP block (1 s) and the dry-run wait's XREAD block (1 s).
REDIS_TIMEOUT_SECONDS = 5.0
POLL_BLOCK_MS = 1000
AUTOCLAIM_EVERY_SECONDS = 60.0


def utcnow() -> datetime:
    return datetime.now(UTC)


def build_providers(
    settings: WorkerSettings, http: httpx.AsyncClient
) -> tuple[LLMProvider, EmbeddingProvider]:
    if settings.llm_provider == "fake":
        # Development without a key: every investigation escalates (nothing is scripted).
        return FakeProvider(model="fake-1"), FakeEmbeddingProvider()
    if settings.gemini_api_key is None:
        raise SystemExit("LLM_PROVIDER=gemini needs GEMINI_API_KEY")
    return (
        GeminiProvider(http, settings.gemini_api_key, settings.llm_model),
        GeminiEmbeddingProvider(http, settings.gemini_api_key),
    )


def run_meta(
    settings: WorkerSettings, provider: LLMProvider, catalogue_version: str
) -> dict[str, Any]:
    """What every incident records in ``agent_meta`` (architecture §6.7)."""
    config = {
        "prompt_version": settings.prompt_version,
        "provider": provider.name,
        "model": provider.model,
        "catalogue_version": catalogue_version,
        "confidence_threshold": settings.confidence_threshold,
        "max_agent_seconds": settings.max_agent_seconds,
        "max_tool_calls": MAX_TOOL_CALLS,
    }
    digest = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    return {
        "prompt_version": settings.prompt_version,
        "provider": provider.name,
        "model": provider.model,
        "catalogue_version": catalogue_version,
        "agent_config_hash": digest,
    }


async def consume(consumer: AgentJobConsumer, stop: asyncio.Event) -> None:
    await consumer.ensure_group()
    recovered = await consumer.recover_own_pending()
    if recovered:
        log.info("handled %d pending agent jobs of this worker", recovered)
    loop = asyncio.get_running_loop()
    next_claim = loop.time()
    while not stop.is_set():
        try:
            if loop.time() >= next_claim:
                await consumer.autoclaim()
                next_claim = loop.time() + AUTOCLAIM_EVERY_SECONDS
            await consumer.poll(POLL_BLOCK_MS)
        except RedisError as exc:
            log.error("agent job queue unavailable: %s", type(exc).__name__)
            await asyncio.sleep(1.0)
    await consumer.drain()


async def consume_results(results: RunnerResultConsumer, stop: asyncio.Event) -> None:
    await results.ensure_group()
    await results.poll(0, pending=True)  # entries this worker read but never acknowledged
    while not stop.is_set():
        try:
            await results.poll(POLL_BLOCK_MS)
        except RedisError as exc:
            log.error("runner results unavailable: %s", type(exc).__name__)
            await asyncio.sleep(1.0)


async def main() -> None:
    configure_logging()
    settings = WorkerSettings()
    link_key = key_from_hex(settings.runner_link_key.get_secret_value())
    catalogue = Catalogue.load(settings.catalogue_path)
    prompts = FilePrompts(settings.prompt_version)
    config_bytes = read_config(settings.detector_config_path)

    pool = await create_pool(settings.database_url.get_secret_value())
    redis: Redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
    )
    async with (
        httpx.AsyncClient() as http,
        httpx.AsyncClient(base_url=docker_base_url(settings.docker_host)) as docker,
    ):
        provider, embedder = build_providers(settings, http)
        events = EventStream(redis)
        prometheus = PrometheusClient(http, settings.prometheus_url)
        loki = LokiClient(http, settings.loki_url)
        toolbox = ToolBox(
            loki=loki,
            prometheus=prometheus,
            docker=docker,
            http=http,
            ledger=settings.ledger_path,
            pool=pool,
            embedder=embedder,
        )
        proposer = RunnerProposer(
            catalogue_path=settings.catalogue_path,
            targets_path=settings.targets_path,
            ledger_path=settings.ledger_path,
            link=RunnerLink(redis, link_key),
        )

        async def investigate(incident_id: UUID) -> None:
            effects = DbEffects(
                pool=pool,
                events=events,
                tools=toolbox,
                incident_id=incident_id,
                provider_name=provider.name,
                model=provider.model,
                proposer=proposer,
                proposal_ttl_seconds=settings.proposal_ttl_seconds,
            )
            await Investigation(
                provider=provider,
                effects=effects,
                prompts=prompts,
                max_seconds=settings.max_agent_seconds,
                threshold=settings.confidence_threshold,
            ).run()

        consumer = AgentJobConsumer(
            redis=redis,
            pool=pool,
            events=events,
            investigate=investigate,
            consumer=socket.gethostname(),
            run_meta=run_meta(settings, provider, catalogue.version),
            concurrency=settings.agent_concurrency,
        )

        async def resets() -> tuple[UUID, datetime] | None:
            record = latest_reset(await asyncio.to_thread(read_records, settings.ledger_path))
            return (record.deploy_id, record.deployed_at) if record else None

        latest = LatestSamples()
        detector = DetectorLoop(
            Detector(load_config(config_bytes), utcnow()),
            LiveSampleSource(load_config(config_bytes), prometheus, loki, docker),
            HttpAlertSink(http, settings.ingest_url, settings.ingest_token),
            resets,
            on_samples=latest.update,
        )
        outcomes = Outcomes(
            pool=pool,
            events=events,
            redis=redis,
            proposer=proposer,
            catalogue_path=settings.catalogue_path,
            clock=utcnow,
            proposal_ttl_seconds=settings.proposal_ttl_seconds,
        )
        results = RunnerResultConsumer(redis, outcomes, link_key, socket.gethostname())
        verifier = SignalVerifier(
            pool,
            outcomes,
            load_config(config_bytes),
            latest,
            utcnow,
            window_seconds=settings.verify_window_seconds,
            timeout_seconds=settings.verify_timeout_seconds,
        )
        sweeper = Sweeper(pool, outcomes, utcnow, settings.execution_timeout_seconds)

        try:
            async with pool.acquire() as conn:
                counts = await ingest_dir(conn, embedder, settings.runbooks_dir)
            log.info("runbooks ingested: %s", counts)
        except (OSError, ValueError, asyncpg.PostgresError, LLMUnavailableError) as exc:
            # The worker still runs; search_runbooks finds less until the next start.
            log.error("runbook ingestion failed: %s", type(exc).__name__)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        background = [
            asyncio.create_task(detector.run(settings.detector_interval_seconds, utcnow)),
            asyncio.create_task(
                every(settings.detector_interval_seconds, verifier.cycle, "verifier")
            ),
            asyncio.create_task(every(SWEEP_EVERY_SECONDS, sweeper.cycle, "sweeper")),
        ]
        results_task = asyncio.create_task(consume_results(results, stop))
        jobs_task = asyncio.create_task(consume(consumer, stop))
        log.info("backend-worker started (provider %s, model %s)", provider.name, provider.model)
        await stop.wait()
        log.info("backend-worker stopping")
        for task in background:
            task.cancel()
        await asyncio.gather(*background, results_task, jobs_task, return_exceptions=True)
    await redis.aclose()
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
