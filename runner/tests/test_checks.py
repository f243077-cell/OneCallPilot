"""A2.1: every check of a runner request fails closed (RUN-001..RUN-004, RUN-018, SEC-002).

Requests are built and signed with tests/publish_signed.py. A refusal must be a
signed `refused` result with the right error_code; nothing is ever accepted
by mistake.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from oncallpilot_runner.catalogue import Catalogue
from oncallpilot_runner.checks import Accepted, Checker, Dropped, Duplicate, Refused
from oncallpilot_runner.handlers import REGISTERED
from oncallpilot_runner.signing import Keys, verify
from oncallpilot_runner.targets import Targets
from tests.conftest import CATALOGUE, NOW, TARGETS, at
from tests.publish_signed import build_request, entry, signed, signed_for


def check(checker: Checker, message: dict[str, Any]) -> Any:
    return checker.check(entry(message))


def assert_refused(decision: Any, code: str, keys: Keys, message: dict[str, Any]) -> None:
    """A refusal: the right code, a valid C5 result, signed with RUNNER_LINK_KEY only."""
    assert isinstance(decision, Refused), decision
    assert decision.error_code == code, decision.reason
    result = decision.result
    assert verify(result, keys.link) and not verify(result, keys.action)
    assert result["status"] == "refused" and result["error_code"] == code
    assert result["request_id"] == message["request_id"]
    if message["type"] == "execute":
        assert result["type"] == "execution_result"
        assert result["execution_id"] == message["execution_id"]
    else:
        assert result["type"] == "dry_run_result" and result["execution_id"] is None
    assert (
        result["dry_run"] is result["step"] is result["health_after"] is result["captured"] is None
    )
    assert result["ts"].endswith("Z")


# --- accepted ---


def test_a_valid_execute_is_accepted(checker: Checker, keys: Keys) -> None:
    message = signed_for(build_request(now=NOW), keys)
    decision = check(checker, message)
    assert isinstance(decision, Accepted)
    assert decision.request.action == "rollback_deploy"
    assert decision.request.params == {"service": "api", "target_release": "1.4.0"}


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("restart_service", {"service": "worker"}),
        ("scale_service", {"service": "api", "replicas": 3}),
        ("clear_cache", {"cache": "pricing"}),
        ("rollback_deploy", {"service": "api", "target_release": "1.4.0"}),
    ],
)
def test_every_enabled_action_is_accepted_as_dry_run_and_execute(
    checker: Checker, keys: Keys, action: str, params: dict[str, Any]
) -> None:
    for kind in ("dry_run", "execute"):
        message = signed_for(build_request(kind, action, params, now=NOW), keys)
        assert isinstance(check(checker, message), Accepted), (kind, action)


# --- signature (RUN-001, SEC-002) ---


def test_a_forged_signature_is_refused(checker: Checker, keys: Keys) -> None:
    message = {**build_request(now=NOW), "sig": "0" * 64}
    assert_refused(check(checker, message), "BAD_SIGNATURE", keys, message)


def test_tampered_params_are_refused(checker: Checker, keys: Keys) -> None:
    message = signed_for(build_request(now=NOW), keys)
    message["params"]["target_release"] = "1.5.0"
    assert_refused(check(checker, message), "BAD_SIGNATURE", keys, message)


def test_an_execute_signed_with_the_link_key_is_refused(checker: Checker, keys: Keys) -> None:
    message = signed_for(build_request(now=NOW), keys, wrong_key=True)
    assert_refused(check(checker, message), "BAD_SIGNATURE", keys, message)


def test_a_dry_run_signed_with_the_action_key_is_refused(checker: Checker, keys: Keys) -> None:
    message = signed_for(build_request("dry_run", now=NOW), keys, wrong_key=True)
    assert_refused(check(checker, message), "BAD_SIGNATURE", keys, message)


def test_an_unknown_key_and_a_missing_sig_are_refused(checker: Checker, keys: Keys) -> None:
    message = signed(build_request(now=NOW), b"k" * 32)
    assert_refused(check(checker, message), "BAD_SIGNATURE", keys, message)
    unsigned = build_request(now=NOW)
    assert_refused(check(checker, unsigned), "BAD_SIGNATURE", keys, unsigned)


def test_the_signature_is_checked_before_anything_else(checker: Checker, keys: Keys) -> None:
    # Expired, disabled and malformed all at once: the signature still decides first.
    message = {
        **build_request(action="run_migration_rollback", now=at(-3600), approved_by=None),
        "sig": "f" * 64,
    }
    assert_refused(check(checker, message), "BAD_SIGNATURE", keys, message)


# --- expiry (RUN-002) ---


def test_a_request_aged_61_seconds_is_refused(
    checker: Checker, keys: Keys, clock: list[datetime]
) -> None:
    message = signed_for(build_request(now=NOW), keys)
    clock[0] = at(61)
    assert_refused(check(checker, message), "REQUEST_EXPIRED", keys, message)
    exact = signed_for(build_request(now=NOW), keys)
    clock[0] = at(60)  # at expires_at exactly: expired too
    assert_refused(check(checker, exact), "REQUEST_EXPIRED", keys, exact)


# --- replay (RUN-005, brought forward) ---


def test_a_replayed_execute_runs_at_most_once(
    checker: Checker, keys: Keys, clock: list[datetime]
) -> None:
    message = signed_for(build_request(now=NOW), keys)
    assert isinstance(check(checker, message), Accepted)
    replay = check(checker, message)
    assert isinstance(replay, Duplicate) and replay.execution_id == message["execution_id"]
    clock[0] = at(120)  # replayed after it expired: its ID is taken, so no answer at all
    assert isinstance(check(checker, message), Duplicate)


def test_a_new_execution_id_is_not_a_duplicate(checker: Checker, keys: Keys) -> None:
    first = signed_for(build_request(now=NOW), keys)
    second = signed_for(build_request(now=NOW), keys)
    assert isinstance(check(checker, first), Accepted)
    assert isinstance(check(checker, second), Accepted)


# --- every final answer to an execute claims its ID first (C5, issue 9) ---


def test_a_forged_message_with_a_real_id_blocks_the_genuine_execute(
    checker: Checker, keys: Keys
) -> None:
    genuine = signed_for(build_request(now=NOW), keys)
    forged = {
        **genuine,
        "params": {"service": "api", "target_release": "1.5.0"},
    }  # sig no longer fits
    assert_refused(check(checker, forged), "BAD_SIGNATURE", keys, forged)
    # The genuine execute arrives after the refusal: a duplicate, never accepted.
    later = check(checker, genuine)
    assert isinstance(later, Duplicate) and later.execution_id == genuine["execution_id"]


def test_a_forged_message_after_the_genuine_one_gets_no_answer(
    checker: Checker, keys: Keys
) -> None:
    genuine = signed_for(build_request(now=NOW), keys)
    assert isinstance(check(checker, genuine), Accepted)
    forged = {**genuine, "sig": "0" * 64}
    # No refusal may be sent for an execution that is already running.
    assert isinstance(check(checker, forged), Duplicate)


@pytest.mark.parametrize(
    "spoil",
    [
        lambda m: {**m, "sig": "1" * 64},  # BAD_SIGNATURE
        lambda m: {**m, "action": "exec_shell", "params": {"cmd": "id"}},  # ACTION_NOT_ALLOWED
    ],
)
def test_every_refused_execute_claims_its_id(
    checker: Checker, keys: Keys, streams: Any, spoil: Any
) -> None:
    message = spoil(signed_for(build_request(now=NOW), keys))
    if message["sig"] != "1" * 64:
        message = signed_for({k: v for k, v in message.items() if k != "sig"}, keys)
    assert isinstance(check(checker, message), Refused)
    assert f"ocp:runner:idem:{message['execution_id']}" in streams.keys


def test_a_refused_dry_run_claims_nothing(checker: Checker, keys: Keys, streams: Any) -> None:
    dry = {**build_request("dry_run", now=NOW), "sig": "2" * 64}
    assert_refused(check(checker, dry), "BAD_SIGNATURE", keys, dry)
    assert streams.keys == {}


# --- catalogue version (C5 check 4) ---


def test_a_different_catalogue_version_is_refused(checker: Checker, keys: Keys) -> None:
    for kind in ("dry_run", "execute"):
        message = signed_for(build_request(kind, now=NOW, catalogue_version="1.1.0"), keys)
        assert_refused(check(checker, message), "VALIDATION_ERROR", keys, message)


# --- catalogue (RUN-003) ---


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("exec_shell", {"cmd": "id"}),
        ("run_migration_rollback", {"migration_id": "0007"}),
        ("delete_container", {"service": "api"}),
    ],
)
def test_unknown_and_disabled_actions_are_refused(
    checker: Checker, keys: Keys, action: str, params: dict[str, Any]
) -> None:
    for kind in ("dry_run", "execute"):
        message = signed_for(build_request(kind, action, params, now=NOW), keys)
        assert_refused(check(checker, message), "ACTION_NOT_ALLOWED", keys, message)


def test_an_enabled_action_without_a_handler_is_refused(checker: Checker, keys: Keys) -> None:
    checker.handlers = REGISTERED - {"clear_cache"}
    message = signed_for(
        build_request("execute", "clear_cache", {"cache": "catalog"}, now=NOW), keys
    )
    assert_refused(check(checker, message), "ACTION_NOT_ALLOWED", keys, message)


def test_the_handler_set_equals_the_enabled_catalogue_set() -> None:
    """RUN-003 / §7.2 CI check; run_migration_rollback is never registered."""
    assert Catalogue.load(CATALOGUE).enabled == REGISTERED
    assert "run_migration_rollback" not in REGISTERED


# --- parameters (RUN-018) ---


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("restart_service", {"service": "api; rm -rf /"}),
        ("restart_service", {"service": "backend-api"}),
        ("restart_service", {"service": "payments"}),
        ("restart_service", {"service": "api", "force": 1}),
        ("restart_service", {}),
        ("scale_service", {"service": "api", "replicas": "3"}),
        ("scale_service", {"service": "api", "replicas": 0}),
        ("scale_service", {"service": "api", "replicas": 6}),
        ("scale_service", {"service": "worker", "replicas": 2}),
        ("rollback_deploy", {"service": "api", "target_release": "1.5.0"}),  # the active one
        ("rollback_deploy", {"service": "api", "target_release": "1.3.0"}),  # no slots
        ("rollback_deploy", {"service": "api", "target_release": "../../etc"}),
        ("rollback_deploy", {"service": "worker", "target_release": "2.2.0"}),  # not in history
        ("clear_cache", {"cache": "*"}),
    ],
)
def test_free_form_or_out_of_range_parameters_are_refused(
    checker: Checker, keys: Keys, action: str, params: dict[str, Any]
) -> None:
    message = signed_for(build_request("execute", action, params, now=NOW), keys)
    assert_refused(check(checker, message), "VALIDATION_ERROR", keys, message)


def test_a_boolean_is_not_an_integer(checker: Checker, keys: Keys) -> None:
    message = signed_for(
        build_request("execute", "scale_service", {"service": "api", "replicas": True}, now=NOW),
        keys,
    )
    assert_refused(check(checker, message), "VALIDATION_ERROR", keys, message)


def test_an_unreadable_ledger_refuses_ledger_parameters(
    checker: Checker, keys: Keys, ledger: Path
) -> None:
    ledger.unlink()
    message = signed_for(build_request(now=NOW), keys)
    assert_refused(check(checker, message), "VALIDATION_ERROR", keys, message)


# --- targets (RUN-004) ---


def test_a_service_missing_from_the_allowlist_is_refused(
    checker: Checker, keys: Keys, tmp_path: Path
) -> None:
    narrow = tmp_path / "targets.yaml"
    narrow.write_text(TARGETS.read_text(encoding="utf-8").split("  redis:")[0], encoding="utf-8")
    checker.targets = Targets.load(narrow)  # the catalogue still lists redis
    message = signed_for(
        build_request("execute", "restart_service", {"service": "redis"}, now=NOW), keys
    )
    assert_refused(check(checker, message), "TARGET_NOT_ALLOWED", keys, message)


def test_a_container_must_match_labels_and_name() -> None:
    targets = Targets.load(TARGETS)
    labels = {"oncallpilot.managed": "true", "oncallpilot.service": "api"}
    assert targets.container_allowed("api", "cs-api-140-1", labels)
    assert not targets.container_allowed("api", "cs-api-140-1", {"oncallpilot.service": "api"})
    assert not targets.container_allowed("api", "backend-api", labels)
    assert not targets.container_allowed("api", "cs-api-140-1x", labels)
    assert not targets.container_allowed("api", "socket-proxy-rw", labels)
    assert not targets.container_allowed("payments", "cs-payments", labels)
    worker = {"oncallpilot.managed": "true", "oncallpilot.service": "worker"}
    assert targets.container_allowed("worker", "cs-worker-210", worker)
    assert not targets.container_allowed("api", "cs-worker-210", worker)  # wrong service


# --- malformed (C5 shape, SEC-002) ---


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"message": "{}"},
        {"msg": "{}", "extra": "x"},
        {"msg": "not json"},
        {"msg": "[1, 2]"},
        {"msg": '{"type": "execute"}'},
        {"msg": '{"type": "shell", "request_id": "3b9d6a1e-5f2c-4e8a-9d71-0c4b8e2f6a13"}'},
    ],
)
def test_entries_too_malformed_to_answer_are_dropped(
    checker: Checker, fields: dict[str, str]
) -> None:
    assert isinstance(checker.check(fields), Dropped)


def test_an_execute_without_a_usable_execution_id_is_dropped(checker: Checker, keys: Keys) -> None:
    message = signed_for(build_request(now=NOW, execution_id="not-a-uuid"), keys)
    assert isinstance(check(checker, message), Dropped)


@pytest.mark.parametrize(
    "overrides",
    [
        {"approved_by": None},
        {"approved_at": None},
        {"idempotency_key": None},
        {"state_fingerprint": None},
        {"proposal_id": None},
        {"state_fingerprint": "XYZ"},
        {"catalogue_version": "v1"},
        {"issued_at": "2026-10-14T11:01:59+02:00"},
        {"expires_at": "yesterday"},
        {"params": ["service", "api"]},
        {"params": {"Service": "api"}},
        {"params": {"service": None}},
        {"params": {"service": 1.5}},
        {"action": "Rollback-Deploy"},
    ],
)
def test_a_signed_execute_with_a_bad_shape_is_refused(
    checker: Checker, keys: Keys, overrides: dict[str, Any]
) -> None:
    message = signed_for({**build_request(now=NOW), **overrides}, keys)
    assert_refused(check(checker, message), "VALIDATION_ERROR", keys, message)


def test_extra_fields_and_dry_runs_with_approval_fields_are_refused(
    checker: Checker, keys: Keys
) -> None:
    extra = signed_for({**build_request(now=NOW), "shell": "id"}, keys)
    assert_refused(check(checker, extra), "VALIDATION_ERROR", keys, extra)
    dry = signed_for(
        build_request("dry_run", now=NOW, approved_by="7e3a1c9b-2d5f-4b8e-a6c0-4f1d8b2e7a95"), keys
    )
    assert_refused(check(checker, dry), "VALIDATION_ERROR", keys, dry)
    backwards = signed_for(
        build_request(
            now=NOW, expires_at=build_request(now=NOW - timedelta(seconds=5))["issued_at"]
        ),
        keys,
    )
    assert_refused(check(checker, backwards), "VALIDATION_ERROR", keys, backwards)


def test_an_unexpected_error_still_refuses(checker: Checker, keys: Keys) -> None:
    message = signed_for(build_request(now=NOW), keys)
    decision = checker.refuse_unexpected(entry(message))
    assert_refused(decision, "VALIDATION_ERROR", keys, message)
