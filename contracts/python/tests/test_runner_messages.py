"""C5 message shapes: what a RunnerRequest and a RunnerResult may and must carry."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from oncallpilot_contracts.runner import RunnerRequest, RunnerResult

VECTORS_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "signing" / "vectors.json"
VECTORS: list[dict[str, Any]] = json.loads(VECTORS_FILE.read_text("utf-8"))["vectors"]


def message(name: str) -> dict[str, Any]:
    vector = next(v for v in VECTORS if v["name"] == name)
    result: dict[str, Any] = json.loads(json.dumps(vector["message"]))  # deep copy
    return result


def execute() -> dict[str, Any]:
    return message("execute_signed_with_action_key")


def dry_run() -> dict[str, Any]:
    return message("dry_run_signed_with_link_key")


def rejects(model: type[BaseModel], data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


# --- requests ---------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "execution_id",
        "proposal_id",
        "state_fingerprint",
        "idempotency_key",
        "approved_by",
        "approved_at",
    ],
)
def test_execute_needs_the_approval(field: str) -> None:
    rejects(RunnerRequest, {**execute(), field: None})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_id", "5c7e9b2d-8a41-4f63-b0d5-2e6a9c1f4b37"),
        ("idempotency_key", "d4f8a2c6-3e1b-4a9d-8c75-1b6e0f3a9d28"),
        ("approved_by", "7e3a1c9b-2d5f-4b8e-a6c0-4f1d8b2e7a95"),
        ("approved_at", "2026-10-14T09:01:58Z"),
    ],
)
def test_dry_run_carries_no_approval(field: str, value: str) -> None:
    rejects(RunnerRequest, {**dry_run(), field: value})


def test_expiry_after_issue() -> None:
    rejects(RunnerRequest, {**execute(), "expires_at": execute()["issued_at"]})


def test_unknown_action_passes_the_shape_check() -> None:
    # The runner answers ACTION_NOT_ALLOWED from the catalogue (RUN-003), so the
    # shape check must let a well-formed but unknown name through.
    RunnerRequest.model_validate({**execute(), "action": "exec_shell"})
    rejects(RunnerRequest, {**execute(), "action": "exec shell; rm -rf /"})


def test_free_text_param_passes_the_shape_check_for_the_runner_to_refuse() -> None:
    # RUN-018: the runner refuses it with VALIDATION_ERROR against the catalogue.
    RunnerRequest.model_validate({**execute(), "params": {"service": "api; rm -rf /"}})


@pytest.mark.parametrize(
    "params",
    [
        {"service": ["api"]},
        {"service": {"name": "api"}},
        {"replicas": True},
        {"replicas": 2.5},
        {"Bad-Name": "api"},
    ],
)
def test_params_are_flat_strings_or_integers(params: dict[str, Any]) -> None:
    rejects(RunnerRequest, {**execute(), "params": params})


def test_request_type_and_sig_shape() -> None:
    rejects(RunnerRequest, {**execute(), "type": "shell"})
    rejects(RunnerRequest, {**execute(), "sig": "ABC"})
    rejects(RunnerRequest, {**execute(), "extra": 1})


# --- results ----------------------------------------------------------------


def test_dry_run_result_rules() -> None:
    ok = message("dry_run_result_signed_with_link_key")
    rejects(RunnerResult, {**ok, "dry_run": None})
    rejects(RunnerResult, {**ok, "execution_id": "5c7e9b2d-8a41-4f63-b0d5-2e6a9c1f4b37"})
    rejects(RunnerResult, {**ok, "status": "succeeded"})
    refused = {**ok, "status": "refused", "dry_run": None}
    rejects(RunnerResult, refused)
    RunnerResult.model_validate({**refused, "error_code": "BAD_SIGNATURE"})


def test_progress_rules() -> None:
    progress = message("progress_with_non_ascii_message")
    rejects(RunnerResult, {**progress, "step": None})
    rejects(RunnerResult, {**progress, "status": "failed"})
    rejects(RunnerResult, {**progress, "captured": {"previous_release": "1.5.0"}})


def test_execution_result_rules() -> None:
    done = message("execution_result_signed_with_link_key")
    rejects(RunnerResult, {**done, "health_after": None})
    failed_check = json.loads(json.dumps(done))
    failed_check["health_after"]["structural"]["passed"] = False
    rejects(RunnerResult, failed_check)
    RunnerResult.model_validate({**failed_check, "status": "failed"})
    rejects(RunnerResult, {**done, "step": {"name": "x", "status": "running", "message": ""}})
    rejects(RunnerResult, {**done, "status": "ok"})


@pytest.mark.parametrize("status", ["aborted", "refused"])
def test_aborted_and_refused_carry_a_code_and_nothing_else(status: str) -> None:
    done = message("execution_result_signed_with_link_key")
    bare = {**done, "status": status, "health_after": None, "captured": None}
    rejects(RunnerResult, bare)
    RunnerResult.model_validate({**bare, "error_code": "STATE_DRIFT"})
    rejects(RunnerResult, {**bare, "error_code": "STATE_DRIFT", "captured": {"x": 1}})


def test_unknown_error_code_is_rejected() -> None:
    done = message("execution_result_signed_with_link_key")
    rejects(
        RunnerResult,
        {**done, "status": "refused", "health_after": None, "captured": None, "error_code": "OOPS"},
    )
