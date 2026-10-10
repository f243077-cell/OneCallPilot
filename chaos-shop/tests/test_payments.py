"""cs-payments (TB-001, ADR-08): charges, adjustable delay, admin token."""

import json
import random
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from common.identity import Identity
from payments.app import create_app
from tests.conftest import ListHandler, capture
from tests.contract import log_problems

IDENTITY = Identity("payments", "1.0.0", "cs-payments")
TOKEN = "p" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Harness:
    def __init__(self, logs: ListHandler, state_file: Path) -> None:
        self.slept: list[float] = []
        self.logs = logs

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)

        self.state_file = state_file
        app = create_app(
            IDENTITY,
            chaos_token=TOKEN,
            sleep=sleep,
            rng=random.Random(1),
            state_file=state_file,
        )
        self.client = TestClient(app)


@pytest.fixture
def payments(tmp_path: Path) -> Iterator[Harness]:
    for logs in capture(IDENTITY):
        yield Harness(logs, tmp_path / "payments.json")


def test_charge_takes_only_the_base_latency_by_default(payments: Harness) -> None:
    response = payments.client.post("/charge", json={"order_id": "o-1", "amount_cents": 1200})
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert 0.02 <= payments.slept[0] <= 0.06


def test_delay_is_added_to_every_charge(payments: Harness) -> None:
    assert payments.client.put("/internal/delay", json={"delay_ms": 2500}, headers=AUTH).json() == {
        "delay_ms": 2500
    }
    payments.client.post("/charge", json={"order_id": "o-2", "amount_cents": 100})
    assert 2.52 <= payments.slept[-1] <= 2.56
    assert payments.client.get("/internal/delay", headers=AUTH).json() == {"delay_ms": 2500}


def test_delay_needs_the_chaos_token_and_is_bounded(payments: Harness) -> None:
    assert payments.client.put("/internal/delay", json={"delay_ms": 10}).status_code == 401
    wrong = {"Authorization": "Bearer " + "x" * 32}
    assert payments.client.get("/internal/delay", headers=wrong).status_code == 401
    too_long = payments.client.put("/internal/delay", json={"delay_ms": 60_000}, headers=AUTH)
    assert too_long.status_code == 422


def test_access_lines_match_the_contract_and_skip_internal(payments: Harness) -> None:
    payments.client.post(
        "/charge",
        json={"order_id": "o-3", "amount_cents": 100},
        headers={"X-Request-ID": "d" * 32},
    )
    payments.client.put("/internal/delay", json={"delay_ms": 0}, headers=AUTH)
    lines = [line for line in payments.logs.lines if '"logger":"shop.' in line]
    records = [json.loads(line) for line in lines]
    assert len(records) == 1
    assert records[0]["request_id"] == "d" * 32
    assert records[0]["status"] == 200
    assert "route" not in records[0]
    assert log_problems(lines[0]) == []


def test_delay_survives_a_restart(payments: Harness) -> None:
    """ADR-19: a restart of cs-payments keeps the delay until chaos reset sets it to 0."""
    payments.client.put("/internal/delay", json={"delay_ms": 2500}, headers=AUTH)
    restarted = create_app(IDENTITY, chaos_token=TOKEN, state_file=payments.state_file)
    with TestClient(restarted) as client:
        assert client.get("/internal/delay", headers=AUTH).json() == {"delay_ms": 2500}
