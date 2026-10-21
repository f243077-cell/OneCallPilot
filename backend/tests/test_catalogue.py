"""Catalogue and live-target validation (B2.8; AI-018, CLAUDE.md §5.6, §6.4, §6.9)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from oncallpilot.catalogue import (
    Catalogue,
    CatalogueError,
    Targets,
    ledger_releases,
    rollback_step,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = Catalogue.load(ROOT / "contracts" / "actions.yaml")
TARGETS = Targets.load(ROOT / "runner" / "targets.yaml")


FIXTURE = (ROOT / "contracts" / "fixtures" / "ledger" / "deploys.jsonl").read_text("utf-8")
TEMPLATE = json.loads(FIXTURE.splitlines()[0])


def ledger(tmp_path: Path, *releases: str, service: str = "api") -> Path:
    """A ledger where the last release listed is the active one."""
    path = tmp_path / "deploys.jsonl"
    lines = [
        json.dumps(
            TEMPLATE
            | {
                "deploy_id": str(uuid.uuid4()),
                "service": service,
                "release": release,
                "image_tag": f"chaos-shop/{service}:{release}",
                "deployed_at": f"2026-10-1{i}T08:00:00Z",
            }
        )
        for i, release in enumerate(releases)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def check(path: Path, action: str, params: dict[str, Any]) -> list[str]:
    _, problems = validate(CATALOGUE, TARGETS, path, action, params)
    return problems


def test_restart_copies_tier_and_requirement_from_the_catalogue(tmp_path: Path) -> None:
    valid, problems = validate(
        CATALOGUE, TARGETS, ledger(tmp_path, "1.4.0"), "restart_service", {"service": "api"}
    )
    assert problems == [] and valid is not None
    assert (valid.action.risk_tier, valid.action.approval_requirement) == ("low", "tap")
    assert valid.catalogue_version == CATALOGUE.version


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("run_migration_rollback", {"migration_id": "0042"}),  # disabled in v1 (§6.4)
        ("exec_shell", {"cmd": "rm -rf /"}),
        ("none", {}),
        ("restart_service", {}),
        ("restart_service", {"service": "api", "force": True}),
        ("restart_service", {"service": "postgres"}),
        ("restart_service", {"service": 1}),
        ("scale_service", {"service": "api", "replicas": 0}),
        ("scale_service", {"service": "api", "replicas": 6}),
        ("scale_service", {"service": "api", "replicas": "3"}),
        ("scale_service", {"service": "api", "replicas": True}),
        ("scale_service", {"service": "worker", "replicas": 2}),
        ("clear_cache", {"cache": "sessions"}),
    ],
)
def test_anything_outside_the_catalogue_is_rejected(
    tmp_path: Path, action: str, params: dict[str, Any]
) -> None:
    assert check(ledger(tmp_path, "1.4.0"), action, params)


def test_rollback_targets_come_from_the_ledger_and_the_slots(tmp_path: Path) -> None:
    path = ledger(tmp_path, "1.3.0", "1.4.0", "1.5.0")
    # 1.3.0 has no slot container; 1.5.0 is active: only 1.4.0 is a rollback target.
    assert ledger_releases(path, TARGETS, "api") == {"1.4.0"}
    assert check(path, "rollback_deploy", {"service": "api", "target_release": "1.4.0"}) == []
    for release in ("1.5.0", "1.3.0", "9.9.9"):
        assert check(path, "rollback_deploy", {"service": "api", "target_release": release})


def test_rollback_with_no_ledger_history_is_rejected(tmp_path: Path) -> None:
    path = ledger(tmp_path, "2.1.0", service="worker")
    assert check(path, "rollback_deploy", {"service": "api", "target_release": "1.4.0"})


def test_live_check_needs_the_target_allowlist(tmp_path: Path) -> None:
    without_redis = Targets({k: v for k, v in TARGETS.releases.items() if k != "redis"})
    _, problems = validate(
        CATALOGUE, without_redis, ledger(tmp_path, "1.4.0"), "restart_service", {"service": "redis"}
    )
    assert problems == ["service 'redis' is not in the target allowlist"]


def test_unreadable_files_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(CatalogueError):
        Catalogue.load(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("actions: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(CatalogueError):
        Catalogue.load(broken)
    with pytest.raises(CatalogueError):
        Targets.load(broken)


def test_rollback_step_is_filled_from_params_and_captured_values() -> None:
    rollback = CATALOGUE.actions["rollback_deploy"]
    assert rollback_step(
        rollback, {"service": "api", "target_release": "1.4.0"}, {"previous_release": "1.5.0"}
    ) == {"action": "rollback_deploy", "params": {"service": "api", "target_release": "1.5.0"}}
    scale = CATALOGUE.actions["scale_service"]
    assert rollback_step(scale, {"service": "api", "replicas": 3}, {"previous_replicas": 1}) == {
        "action": "scale_service",
        "params": {"service": "api", "replicas": 1},
    }
    assert rollback_step(scale, {"service": "api", "replicas": 3}, {}) is None
    assert rollback_step(CATALOGUE.actions["restart_service"], {"service": "api"}, {}) is None
