"""cs-api routes: /products, /cart, /checkout, /orders, /healthz, /metrics, /internal/cache/*.

Every request except /metrics and /internal/* is counted in the C7 http metrics
and gets one access line (logger ``chaosshop.access``): INFO below 500, ERROR
from 500 up. Internal endpoints are neither counted nor access-logged.
"""

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.deps import (
    CacheName,
    CacheUnavailable,
    CartLine,
    Deps,
    Order,
    PaymentFailed,
    StoreUnavailable,
)
from api.metrics import ApiMetrics, is_counted, method_label, route_label
from common.auth import bearer_guard
from common.identity import Identity
from common.jsonlog import request_id_var, route_var
from common.metrics import CONTENT_TYPE

REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")

log_access = logging.getLogger("chaosshop.access")
log_app = logging.getLogger("chaosshop.app")
log_catalog = logging.getLogger("chaosshop.catalog")
log_cart = logging.getLogger("chaosshop.cart")
log_checkout = logging.getLogger("chaosshop.checkout")
log_orders = logging.getLogger("chaosshop.orders")
log_admin = logging.getLogger("chaosshop.admin")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CartItemIn(_Strict):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=10)


class CartIn(_Strict):
    items: list[CartItemIn] = Field(min_length=1, max_length=10)


class CheckoutIn(_Strict):
    cart_id: UUID


class CacheClearIn(_Strict):
    cache: CacheName


def _error(status: int, code: str, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": code, **extra}, status_code=status)


def _order_out(order: Order) -> dict[str, Any]:
    return {
        "order_id": str(order.id),
        "cart_id": str(order.cart_id),
        "total_cents": order.total_cents,
        "status": order.status,
        "created_at": order.created_at,
    }


def _lines_out(lines: Sequence[CartLine]) -> list[dict[str, int]]:
    return [asdict(line) for line in lines]


def create_app(
    identity: Identity, deps: Deps, metrics: ApiMetrics, *, runner_admin_token: str
) -> FastAPI:
    store, cache, payments = deps.store, deps.cache, deps.payments
    metrics.watch_pool(store.pool_counts)
    admin = Depends(bearer_guard(runner_admin_token))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await store.start()
        await cache.start()
        await payments.start()
        try:
            yield
        finally:
            await payments.close()
            await cache.close()
            await store.close()

    app = FastAPI(
        title="Chaos Shop API",
        version=identity.release,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def count_cache(name: str, result: str) -> None:
        metrics.cache_operations.labels(cache=name, result=result).inc()

    @app.middleware("http")
    async def telemetry(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not is_counted(path):
            return await call_next(request)
        header = request.headers.get("x-request-id", "")
        request_id = header if REQUEST_ID.fullmatch(header) else uuid4().hex
        route = route_label(path)
        method = method_label(request.method)
        id_token = request_id_var.set(request_id)
        route_token = route_var.set(route)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                log_app.error("unhandled error while serving the request", exc_info=True)
                response = _error(500, "INTERNAL_ERROR")
            elapsed = time.perf_counter() - started
            status = response.status_code
            metrics.http_requests.labels(route=route, method=method, status=str(status)).inc()
            metrics.http_duration.labels(route=route, method=method).observe(elapsed)
            log_access.log(
                logging.ERROR if status >= 500 else logging.INFO,
                "%s %s %d",
                method,
                route,
                status,
                extra={"status": status, "duration_ms": round(elapsed * 1000, 1)},
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            route_var.reset(route_token)
            request_id_var.reset(id_token)

    async def lookup_prices(product_ids: Sequence[int]) -> dict[int, int]:
        cache_ok = True
        try:
            cached = await cache.get_prices(product_ids)
        except CacheUnavailable:
            count_cache("pricing", "error")
            log_cart.error(
                "pricing cache unavailable, reading prices from the database", exc_info=True
            )
            cached, cache_ok = None, False
        else:
            count_cache("pricing", "hit" if cached is not None else "miss")
        if cached is not None:
            return cached
        prices = await store.prices(product_ids)
        if cache_ok:
            try:
                await cache.set_prices(prices)
            except CacheUnavailable:
                log_cart.error("could not refill the pricing cache", exc_info=True)
        return prices

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": identity.service, "release": identity.release}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(metrics.render(), media_type=CONTENT_TYPE)

    @app.get("/products", response_model=None)
    async def products() -> dict[str, Any] | JSONResponse:
        cache_ok = True
        try:
            cached = await cache.get_catalog()
        except CacheUnavailable:
            count_cache("catalog", "error")
            log_catalog.error("catalog cache unavailable, reading the database", exc_info=True)
            cached, cache_ok = None, False
        else:
            count_cache("catalog", "hit" if cached is not None else "miss")
        if cached is not None:
            return {"products": cached}
        try:
            items = [asdict(p) for p in await store.list_products()]
        except StoreUnavailable:
            log_catalog.error("could not load products", exc_info=True)
            return _error(503, "STORE_UNAVAILABLE")
        if cache_ok:
            try:
                await cache.set_catalog(items)
            except CacheUnavailable:
                log_catalog.error("could not refill the catalog cache", exc_info=True)
        return {"products": items}

    @app.post("/cart", status_code=201, response_model=None)
    async def create_cart(body: CartIn) -> dict[str, Any] | JSONResponse:
        quantities: dict[int, int] = {}
        for item in body.items:
            quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
        product_ids = sorted(quantities)
        try:
            prices = await lookup_prices(product_ids)
            unknown = [pid for pid in product_ids if pid not in prices]
            if unknown:
                return _error(422, "UNKNOWN_PRODUCT", product_ids=unknown)
            lines = [CartLine(pid, quantities[pid], prices[pid]) for pid in product_ids]
            cart_id = await store.create_cart(lines)
        except StoreUnavailable:
            log_cart.error("could not create the cart", exc_info=True)
            return _error(503, "STORE_UNAVAILABLE")
        total = sum(line.quantity * line.price_cents for line in lines)
        return {"cart_id": str(cart_id), "total_cents": total, "items": _lines_out(lines)}

    @app.get("/cart", response_model=None)
    async def get_cart(cart_id: UUID) -> dict[str, Any] | JSONResponse:
        try:
            lines = await store.get_cart(cart_id)
        except StoreUnavailable:
            log_cart.error("could not read the cart", exc_info=True)
            return _error(503, "STORE_UNAVAILABLE")
        if lines is None:
            return _error(404, "CART_NOT_FOUND")
        total = sum(line.quantity * line.price_cents for line in lines)
        return {"cart_id": str(cart_id), "total_cents": total, "items": _lines_out(lines)}

    @app.post("/checkout", status_code=201, response_model=None)
    async def checkout(body: CheckoutIn) -> dict[str, Any] | JSONResponse:
        try:
            order = await store.create_order(body.cart_id)
        except StoreUnavailable:
            log_checkout.error("could not create the order", exc_info=True)
            return _error(503, "STORE_UNAVAILABLE")
        if order is None:
            return _error(404, "CART_NOT_FOUND")

        started = time.perf_counter()
        try:
            await payments.charge(order.id, order.total_cents, request_id_var.get() or "")
        except PaymentFailed:
            metrics.upstream_duration.labels(upstream="payments").observe(
                time.perf_counter() - started
            )
            log_checkout.error("payment failed for order %s", order.id, exc_info=True)
            try:
                await store.set_order_status(order.id, "payment_failed")
            except StoreUnavailable:
                log_checkout.error("could not record the failed payment", exc_info=True)
            return _error(502, "PAYMENT_FAILED", order_id=str(order.id))
        metrics.upstream_duration.labels(upstream="payments").observe(time.perf_counter() - started)

        try:
            await store.set_order_status(order.id, "paid")
        except StoreUnavailable:
            log_checkout.error("could not mark order %s as paid", order.id, exc_info=True)
            return _error(503, "STORE_UNAVAILABLE", order_id=str(order.id))
        try:
            for job_type in ("fulfil_order", "send_receipt"):
                await cache.enqueue({"type": job_type, "order_id": str(order.id)})
        except CacheUnavailable:
            log_checkout.error("could not queue jobs for order %s", order.id, exc_info=True)
            return _error(503, "ORDER_QUEUE_UNAVAILABLE", order_id=str(order.id))
        return {"order_id": str(order.id), "total_cents": order.total_cents, "status": "paid"}

    @app.get("/orders", response_model=None)
    async def orders(limit: int = Query(default=10, ge=1, le=50)) -> dict[str, Any] | JSONResponse:
        try:
            recent = await store.list_orders(limit)
        except StoreUnavailable:
            log_orders.error("could not list orders", exc_info=True)
            return _error(503, "STORE_UNAVAILABLE")
        return {"orders": [_order_out(o) for o in recent]}

    @app.post("/internal/cache/clear", dependencies=[admin], response_model=None)
    async def cache_clear(body: CacheClearIn) -> dict[str, Any] | JSONResponse:
        try:
            cleared = await cache.clear(body.cache)
        except CacheUnavailable:
            log_admin.error("could not clear the %s cache", body.cache, exc_info=True)
            return _error(503, "CACHE_UNAVAILABLE")
        log_admin.info("%s cache cleared", body.cache)
        return {"cache": body.cache, "cleared": cleared}

    @app.get("/internal/cache/stats", dependencies=[admin], response_model=None)
    async def cache_stats() -> dict[str, Any] | JSONResponse:
        try:
            return await cache.stats()
        except CacheUnavailable:
            return _error(503, "CACHE_UNAVAILABLE")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail).upper().replace(" ", "_"))

    return app
