"""cs-api behaviour with in-memory fakes (TB-001)."""

import json
from typing import Any

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from api.app import create_app
from api.deps import Deps
from api.faults import Faults
from api.metrics import ApiMetrics
from tests.conftest import ADMIN_TOKEN, API_IDENTITY, ApiHarness
from tests.fakes import FakeCache, FakePayments, FakeStore, store_unavailable


def sample_value(api: ApiHarness, name: str, **labels: str) -> float:
    text = api.client.get("/metrics").text
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == name and all(sample.labels.get(k) == v for k, v in labels.items()):
                return float(sample.value)
    return 0.0


def access_lines(api: ApiHarness) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in api.logs.lines]
    return [r for r in records if r["logger"] == "chaosshop.access"]


def new_cart(api: ApiHarness) -> str:
    response = api.client.post("/cart", json={"items": [{"product_id": 1, "quantity": 2}]})
    assert response.status_code == 201
    cart_id: str = response.json()["cart_id"]
    return cart_id


def test_healthz(api: ApiHarness) -> None:
    response = api.client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "api", "release": "1.4.0"}


def test_products_miss_then_hit(api: ApiHarness) -> None:
    first = api.client.get("/products")
    second = api.client.get("/products")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(first.json()["products"]) == 5
    assert sample_value(api, "cache_operations_total", cache="catalog", result="miss") == 1
    assert sample_value(api, "cache_operations_total", cache="catalog", result="hit") == 1


def test_products_fall_back_to_database_when_cache_is_down(api: ApiHarness) -> None:
    api.cache.down = True
    response = api.client.get("/products")
    assert response.status_code == 200
    assert sample_value(api, "cache_operations_total", cache="catalog", result="error") == 1
    errors = [json.loads(line) for line in api.logs.lines if '"level":"ERROR"' in line]
    assert errors and errors[0]["logger"] == "chaosshop.catalog"
    assert errors[0]["exc_type"] == "CacheUnavailable"
    assert errors[0]["route"] == "/products"


def test_cart_create_and_read(api: ApiHarness) -> None:
    response = api.client.post(
        "/cart",
        json={
            "items": [
                {"product_id": 1, "quantity": 2},
                {"product_id": 2, "quantity": 1},
                {"product_id": 1, "quantity": 1},
            ]
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["total_cents"] == 3 * 1001 + 1002
    read = api.client.get("/cart", params={"cart_id": body["cart_id"]})
    assert read.status_code == 200
    assert read.json()["total_cents"] == body["total_cents"]
    # The second lookup of the same products is a pricing cache hit.
    api.client.post("/cart", json={"items": [{"product_id": 2, "quantity": 1}]})
    assert sample_value(api, "cache_operations_total", cache="pricing", result="hit") == 1


def test_cart_rejects_unknown_products_and_bad_input(api: ApiHarness) -> None:
    unknown = api.client.post("/cart", json={"items": [{"product_id": 99, "quantity": 1}]})
    assert unknown.status_code == 422
    assert unknown.json()["error"] == "UNKNOWN_PRODUCT"
    extra = api.client.post("/cart", json={"items": [], "admin": True})
    assert extra.status_code == 422
    missing = api.client.get("/cart", params={"cart_id": "00000000-0000-0000-0000-000000000000"})
    assert missing.status_code == 404


def test_checkout_charges_and_queues_jobs(api: ApiHarness) -> None:
    cart_id = new_cart(api)
    response = api.client.post(
        "/checkout", json={"cart_id": cart_id}, headers={"X-Request-ID": "b" * 32}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "paid"
    assert [job["type"] for job in api.cache.jobs] == ["fulfil_order", "send_receipt"]
    assert api.payments.calls[0][2] == "b" * 32  # the request id is forwarded
    count = sample_value(api, "upstream_request_duration_seconds_count", upstream="payments")
    assert count == 1


def test_checkout_payment_failure_is_502(api: ApiHarness) -> None:
    cart_id = new_cart(api)
    api.payments.fail = True
    response = api.client.post("/checkout", json={"cart_id": cart_id})
    assert response.status_code == 502
    assert response.json()["error"] == "PAYMENT_FAILED"
    assert [o.status for o in api.store.orders.values()] == ["payment_failed"]
    assert not api.cache.jobs


def test_checkout_without_queue_is_503(api: ApiHarness) -> None:
    cart_id = new_cart(api)
    api.cache.down = True
    response = api.client.post("/checkout", json={"cart_id": cart_id})
    assert response.status_code == 503
    assert response.json()["error"] == "ORDER_QUEUE_UNAVAILABLE"


def test_store_unavailable_is_503_with_error_access_line(api: ApiHarness) -> None:
    api.store.fail = store_unavailable()
    response = api.client.get("/orders")
    assert response.status_code == 503
    line = access_lines(api)[-1]
    assert line["level"] == "ERROR"
    assert line["status"] == 503
    assert line["route"] == "/orders"
    assert sample_value(api, "http_requests_total", route="/orders", method="GET", status="503")


def test_unhandled_error_is_logged_with_stacktrace(api: ApiHarness) -> None:
    api.store.fail = RuntimeError("unexpected state")
    response = api.client.get("/orders")
    assert response.status_code == 500
    records = [json.loads(line) for line in api.logs.lines]
    errors = [r for r in records if r.get("exc_type") == "RuntimeError"]
    assert errors and errors[0]["stacktrace"].startswith("Traceback (most recent call last):")
    assert errors[0]["level"] == "ERROR"


def test_request_id_header_is_kept_only_when_valid(api: ApiHarness) -> None:
    kept = api.client.get("/healthz", headers={"X-Request-ID": "c" * 32})
    assert kept.headers["X-Request-ID"] == "c" * 32
    replaced = api.client.get("/healthz", headers={"X-Request-ID": "not-a-valid-id"})
    assert replaced.headers["X-Request-ID"] != "not-a-valid-id"
    assert len(replaced.headers["X-Request-ID"]) == 32


def test_unknown_path_is_counted_as_other(api: ApiHarness) -> None:
    response = api.client.get("/products/123")
    assert response.status_code == 404
    assert sample_value(api, "http_requests_total", route="other", method="GET", status="404")
    assert access_lines(api)[-1]["route"] == "other"


def test_odd_methods_are_counted_as_other(api: ApiHarness) -> None:
    api.client.request("PATCH", "/products")
    assert sample_value(api, "http_requests_total", route="/products", method="OTHER", status="405")


def test_internal_cache_endpoints_need_the_runner_token(api: ApiHarness) -> None:
    assert api.client.get("/internal/cache/stats").status_code == 401
    wrong = {"Authorization": "Bearer " + "b" * 32}
    assert api.client.get("/internal/cache/stats", headers=wrong).status_code == 401
    # Tokens are never accepted in the URL (CLAUDE.md §6.12).
    in_url = api.client.get("/internal/cache/stats", params={"token": ADMIN_TOKEN})
    assert in_url.status_code == 401
    good = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    api.client.get("/products")
    assert api.client.get("/internal/cache/stats", headers=good).json()["catalog"]["present"]
    cleared = api.client.post("/internal/cache/clear", json={"cache": "catalog"}, headers=good)
    assert cleared.json() == {"cache": "catalog", "cleared": True}
    unknown = api.client.post("/internal/cache/clear", json={"cache": "sessions"}, headers=good)
    assert unknown.status_code == 422


def test_internal_endpoints_refuse_without_a_configured_token() -> None:
    metrics = ApiMetrics(API_IDENTITY)
    app = create_app(
        API_IDENTITY,
        Deps(FakeStore(), FakeCache(), FakePayments()),
        metrics,
        runner_admin_token="",
        chaos_token="",
        faults=Faults(),
    )
    with TestClient(app) as client:
        response = client.get("/internal/cache/stats", headers={"Authorization": "Bearer "})
        assert response.status_code == 503


def test_metrics_and_internal_paths_are_not_counted_or_logged(api: ApiHarness) -> None:
    good = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    api.client.get("/internal/cache/stats", headers=good)
    api.client.get("/metrics")
    text = api.client.get("/metrics").text
    assert "/internal" not in text and 'route="/metrics"' not in text
    assert access_lines(api) == []
