"""TB-009: every ledger line the chaos CLI writes is a valid C6 DeployRecord."""

import json
from datetime import UTC, datetime

import pytest
from oncallpilot_contracts.ledger import ALLOWED_WRITERS, DeployRecord

from chaos_cli import ledger
from chaos_cli.config import REPO_ROOT
from chaos_cli.ledger import Releases

FIXED = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def releases() -> Releases:
    return Releases.load()


def test_seeded_ledger_is_history_then_one_reset_per_service(releases: Releases) -> None:
    records = ledger.seeded_ledger(releases, now=lambda: FIXED)
    history, resets = records[:-2], records[-2:]
    assert len(history) == len(releases.history) >= 3
    assert all(r.kind == "deploy" and r.deployed_by == "setup" for r in history)
    assert [r.deployed_at for r in history] == sorted(r.deployed_at for r in history)
    assert [(r.kind, r.service, r.release) for r in resets] == [
        ("reset", "api", releases.baseline["api"]),
        ("reset", "worker", releases.baseline["worker"]),
    ]
    assert all(r.deployed_by == "setup" and r.deployed_at == FIXED for r in resets)
    assert ledger.current(records, "api").release == "1.4.0"  # type: ignore[union-attr]


def test_history_agrees_with_the_release_entries(releases: Releases) -> None:
    for entry in releases.history:
        known = releases.releases[entry["service"]].get(entry["release"])
        if known is not None:
            assert entry["commit_sha"] == known["commit_sha"]
            assert entry["commit_message"] == known["commit_message"]
            assert entry["config_hash"] == ledger.config_hash(known["config"])
    # One SHA per release and one release per SHA, all lower-case 40-character hex.
    pairs = {(e["service"], e["release"], e["commit_sha"]) for e in releases.history}
    pairs |= {
        (service, release, entry["commit_sha"])
        for service, entries in releases.releases.items()
        for release, entry in entries.items()
    }
    assert len({(s, r) for s, r, _ in pairs}) == len({sha for _, _, sha in pairs}) == len(pairs)
    assert all(len(sha) == 40 and sha == sha.lower() for _, _, sha in pairs)


def test_contract_ledger_fixture_agrees_with_releases(releases: Releases) -> None:
    """contracts/fixtures/ledger/deploys.jsonl describes this testbed, so mock mode,
    recorded fixtures and the live ledger show the same releases."""
    fixture = REPO_ROOT / "contracts" / "fixtures" / "ledger" / "deploys.jsonl"
    known = {
        (e["service"], e["release"]): (e["commit_sha"], e["commit_message"], e["config_hash"])
        for e in releases.history
    }
    for service, entries in releases.releases.items():
        for release, entry in entries.items():
            known[(service, release)] = (
                entry["commit_sha"],
                entry["commit_message"],
                ledger.config_hash(entry["config"]),
            )
    for record in ledger.parse_lines(fixture.read_text(encoding="utf-8")):
        expected = known[(record.service, record.release)]
        assert (record.commit_sha, record.commit_message, record.config_hash) == expected
        if record.kind == "reset":
            assert record.reason == ledger.REASON_RESET


def test_every_written_line_validates_and_uses_allowed_writers(releases: Releases) -> None:
    records = [
        *ledger.seeded_ledger(releases),
        ledger.make_record(releases, kind="deploy", service="api", release="1.5.0",
                           replicas=2, deployed_by="ci", reason=ledger.REASON_RELEASE),
        ledger.make_record(releases, kind="deploy", service="worker", release="2.2.0",
                           replicas=1, deployed_by="ci", reason=ledger.REASON_RELEASE),
        ledger.make_record(releases, kind="rollback", service="api", release="1.4.0",
                           replicas=2, deployed_by="setup", reason=ledger.REASON_ROLLBACK),
        ledger.make_record(releases, kind="scale", service="api", release="1.4.0",
                           replicas=3, deployed_by="setup", reason=ledger.REASON_SCALE),
    ]  # fmt: skip
    text = "".join(ledger.to_line(r) + "\n" for r in records)
    for line in text.splitlines():
        record = DeployRecord.model_validate_json(line)
        assert record.deployed_by in ALLOWED_WRITERS[record.kind]
        assert json.loads(line)["deployed_at"].endswith("Z")
    assert ledger.parse_lines(text) == records


def test_config_hash_changes_only_with_the_config(releases: Releases) -> None:
    api_140 = ledger.make_record(
        releases,
        kind="deploy",
        service="api",
        release="1.4.0",
        replicas=1,
        deployed_by="setup",
        reason="r",
    )
    api_150 = ledger.make_record(
        releases,
        kind="deploy",
        service="api",
        release="1.5.0",
        replicas=1,
        deployed_by="ci",
        reason="r",
    )
    w_210 = ledger.make_record(
        releases,
        kind="deploy",
        service="worker",
        release="2.1.0",
        replicas=1,
        deployed_by="setup",
        reason="r",
    )
    w_220 = ledger.make_record(releases, kind="deploy", service="worker", release="2.2.0",
                               replicas=1, deployed_by="ci", reason="r")  # fmt: skip
    assert api_140.config_hash == api_150.config_hash  # a code change only
    assert w_210.config_hash != w_220.config_hash  # scenario 6 changes the config


def test_a_trailing_partial_line_is_ignored(releases: Releases) -> None:
    line = ledger.to_line(ledger.seeded_ledger(releases)[0])
    assert len(ledger.parse_lines(line + "\n" + line[:40])) == 1
