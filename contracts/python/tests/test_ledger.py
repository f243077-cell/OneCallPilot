import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from oncallpilot_contracts.ledger import DeployRecord
from oncallpilot_contracts.schema_export import SCHEMAS, render_schema

CONTRACTS_DIR = Path(__file__).resolve().parents[2]
FIXTURE = CONTRACTS_DIR / "fixtures" / "ledger" / "deploys.jsonl"

# TB-010: agent-visible ledger fields must not name the fault.
FORBIDDEN_WORDS = re.compile(r"bad|leak|broken|chaos|fault|inject", re.IGNORECASE)


def fixture_lines() -> list[str]:
    return FIXTURE.read_text("utf-8").splitlines()


def valid_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(fixture_lines()[5])  # the ci deploy of api 1.5.0
    record.update(overrides)
    return record


def test_every_fixture_line_validates() -> None:
    lines = fixture_lines()
    assert len(lines) >= 8
    for line in lines:
        DeployRecord.model_validate_json(line)


def test_fixture_has_every_kind_and_writer() -> None:
    records = [DeployRecord.model_validate_json(line) for line in fixture_lines()]
    assert {r.kind for r in records} == {"deploy", "rollback", "scale", "reset"}
    assert {r.deployed_by for r in records} == {"ci", "runner", "setup"}


def test_fixture_fields_are_neutral() -> None:
    for line in fixture_lines():
        record = DeployRecord.model_validate_json(line)
        visible = " ".join(
            [record.release, record.commit_message, record.deployed_by, record.reason]
        )
        assert not FORBIDDEN_WORDS.search(visible), visible


def test_round_trip_is_stable_and_uses_z_suffix() -> None:
    line = fixture_lines()[5]
    record = DeployRecord.model_validate_json(line)
    assert json.loads(record.model_dump_json()) == json.loads(line)
    assert record.model_dump_json().count('"deployed_at":"2026-10-14T08:20:00Z"') == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"deployed_by": "chaos"},
        {"deployed_by": "human"},
        {"kind": "restart"},
        {"service": "payments"},
        {"service": "redis"},
        {"release": "1.5"},
        {"release": "v1.5.0"},
        {"commit_sha": "9F2C7B1E5D3A8C6F4E2B0D9A7C5E3F1B8D6A4C2E"},
        {"commit_sha": "9f2c7b1"},
        {"config_hash": "sha256"},
        {"image_tag": "chaos-shop/api:1.4.0"},
        {"image_tag": "chaos-shop/worker:1.5.0"},
        {"image_tag": "evil/api:1.5.0"},
        {"replicas": 0},
        {"replicas": 6},
        {"deployed_at": "2026-10-14T08:20:00"},
        {"deployed_at": "2026-10-14T10:20:00+02:00"},
        {"deploy_id": "not-a-uuid"},
        {"commit_message": ""},
        {"commit_message": "x" * 201},
        {"reason": ""},
        {"reason": "x" * 201},
        {"extra_field": "x"},
    ],
)
def test_invalid_field_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DeployRecord.model_validate(valid_record(**overrides))


@pytest.mark.parametrize(
    ("kind", "deployed_by"),
    [
        ("reset", "ci"),
        ("reset", "runner"),
        ("rollback", "ci"),
        ("scale", "ci"),
        ("deploy", "runner"),
    ],
)
def test_kind_and_writer_must_match(kind: str, deployed_by: str) -> None:
    with pytest.raises(ValidationError):
        DeployRecord.model_validate(valid_record(kind=kind, deployed_by=deployed_by))


def test_worker_runs_exactly_one_replica() -> None:
    worker = json.loads(fixture_lines()[1])
    DeployRecord.model_validate(worker)
    with pytest.raises(ValidationError):
        DeployRecord.model_validate({**worker, "replicas": 2})


def test_record_is_frozen() -> None:
    record = DeployRecord.model_validate(valid_record())
    with pytest.raises(ValidationError):
        record.replicas = 2  # type: ignore[misc]


def test_committed_schema_matches_model() -> None:
    assert "deploy_record" in SCHEMAS
    for name, model in SCHEMAS.items():
        committed = (CONTRACTS_DIR / "schemas" / f"{name}.json").read_text("utf-8")
        assert committed == render_schema(model), f"regenerate contracts/schemas/{name}.json"
