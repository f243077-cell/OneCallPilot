"""C2: openapi.yaml covers §9.2 exactly, every $ref resolves, and the C2 bodies validate."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError
from samples import INCIDENT_ID, incident_summary, seed_evidence

from oncallpilot_contracts import api, enums
from oncallpilot_contracts.schema_export import SCHEMAS

CONTRACTS_DIR = Path(__file__).resolve().parents[2]
SPEC: dict[str, Any] = yaml.safe_load((CONTRACTS_DIR / "openapi.yaml").read_text("utf-8"))
METHODS = {"get", "put", "post", "delete", "patch"}

# architecture §9.2, without the WebSocket row (that is C3).
EXPECTED_OPERATIONS = {
    ("get", "/healthz"),
    ("post", "/ingest/alert"),
    ("get", "/incidents"),
    ("get", "/incidents/{id}"),
    ("get", "/incidents/{id}/log-tail"),
    ("post", "/incidents/{id}/resolve"),
    ("post", "/proposals/{id}/challenge"),
    ("post", "/proposals/{id}/approve"),
    ("post", "/proposals/{id}/reject"),
    ("post", "/devices"),
    ("delete", "/devices/{id}"),
    ("get", "/audit"),
    ("get", "/settings"),
    ("put", "/settings"),
}


def operations() -> Iterator[tuple[str, str, dict[str, Any]]]:
    for path, item in SPEC["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                yield method, path, operation


def refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs(value)


def resolve(ref: str) -> Any:
    assert ref.startswith("#/"), ref
    node: Any = SPEC
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def test_version_and_dialect() -> None:
    assert SPEC["openapi"] == "3.1.0"
    assert SPEC["info"]["version"] == (CONTRACTS_DIR / "VERSION").read_text("utf-8").strip()


def test_operations_match_architecture() -> None:
    assert {(method, path) for method, path, _ in operations()} == EXPECTED_OPERATIONS


def test_operation_ids_are_unique() -> None:
    ids = [operation["operationId"] for _, _, operation in operations()]
    assert len(ids) == len(set(ids))


def test_every_ref_resolves() -> None:
    for ref in refs(SPEC):
        if ref.startswith("#/"):
            resolve(ref)
        else:
            file_part = ref.split("#")[0]
            assert (CONTRACTS_DIR / file_part).is_file(), ref
            assert file_part.startswith("schemas/") and Path(file_part).stem in SCHEMAS, ref


def response_schema_ref(response: dict[str, Any]) -> str | None:
    if "$ref" in response:
        response = resolve(response["$ref"])
    content = response.get("content")
    if content is None:
        return None
    ref: str = content["application/json"]["schema"]["$ref"]
    return ref


def test_every_error_uses_the_envelope() -> None:
    for method, path, operation in operations():
        for status, response in operation["responses"].items():
            if int(status) < 400 or path == "/healthz":
                continue
            assert response_schema_ref(response) == "schemas/error_envelope.json", (
                method,
                path,
                status,
            )


def test_auth_per_operation() -> None:
    assert SPEC["security"] == [{"bearerAuth": []}]
    for method, path, operation in operations():
        if path == "/healthz":
            assert operation["security"] == []
        elif path == "/ingest/alert":
            assert operation["security"] == [{"ingestToken": []}]
        else:
            assert "security" not in operation, (method, path)  # the global bearerAuth
            assert "401" in operation["responses"], (method, path)


def test_approve_and_reject_require_an_idempotency_key() -> None:
    key = resolve("#/components/parameters/IdempotencyKey")
    assert key["in"] == "header" and key["required"] is True
    for path in ("/proposals/{id}/approve", "/proposals/{id}/reject"):
        params = SPEC["paths"][path]["post"]["parameters"]
        assert {"$ref": "#/components/parameters/IdempotencyKey"} in params
        assert "400" in SPEC["paths"][path]["post"]["responses"]


def test_no_token_in_any_url() -> None:
    # SEC-011: tokens travel only in headers.
    for _, _, operation in operations():
        for param in operation.get("parameters", []):
            param = resolve(param["$ref"]) if "$ref" in param else param
            if param["in"] in ("query", "path"):
                assert "token" not in param["name"].lower()


def test_query_enums_match_the_models() -> None:
    params = {p["name"]: p for p in SPEC["paths"]["/incidents"]["get"]["parameters"] if "name" in p}
    assert params["status"]["schema"]["enum"] == list(get_args(enums.IncidentStatus))
    assert params["service"]["schema"]["enum"] == list(get_args(enums.ServiceName))


# --- C2 bodies ---------------------------------------------------------------


def test_alert_in_example_from_architecture() -> None:
    api.AlertIn.model_validate(
        {
            "source": "detector",
            "service": "api",
            "signals": seed_evidence()["payload"]["signals"],
            "observed_at": "2026-10-14T09:00:25Z",
        }
    )


def test_healthz_status_follows_dependencies() -> None:
    ok = {"status": "ok", "contracts_version": "0.1.0", "db": "up", "redis": "up"}
    api.Healthz.model_validate(ok)
    api.Healthz.model_validate({**ok, "status": "degraded", "redis": "down"})
    with pytest.raises(ValidationError):
        api.Healthz.model_validate({**ok, "redis": "down"})


def test_error_envelope() -> None:
    api.ErrorEnvelope.model_validate(
        {"error": {"code": "COOLDOWN_ACTIVE", "message": "wait", "details": {"retry_after": 87}}}
    )
    with pytest.raises(ValidationError):
        api.ErrorEnvelope.model_validate(
            {"error": {"code": "TEAPOT", "message": "no", "details": {}}}
        )


def test_incident_page() -> None:
    api.IncidentPage.model_validate({"items": [incident_summary()], "next_cursor": None})
    with pytest.raises(ValidationError):
        api.IncidentPage.model_validate({"items": [incident_summary()] * 101, "next_cursor": None})


def test_ingest_accepted_and_resolve() -> None:
    api.IngestAccepted.model_validate({"incident_id": INCIDENT_ID, "deduplicated": True})
    with pytest.raises(ValidationError):
        api.ResolveRequest.model_validate({"reason": "ok"})
