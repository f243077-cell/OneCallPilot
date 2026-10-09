"""HTTP client for cs-payments, the simulated third-party payment provider."""

from uuid import UUID

import httpx

from api.deps import PaymentFailed


class HttpPayments:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(timeout))

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        await self._client.aclose()

    async def charge(self, order_id: UUID, amount_cents: int, request_id: str) -> str:
        try:
            response = await self._client.post(
                "/charge",
                json={"order_id": str(order_id), "amount_cents": amount_cents},
                headers={"X-Request-ID": request_id},
            )
        except httpx.HTTPError as exc:
            raise PaymentFailed("payment provider unreachable") from exc
        if response.status_code != 200:
            raise PaymentFailed(f"payment provider returned HTTP {response.status_code}")
        try:
            charge_id = response.json()["charge_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise PaymentFailed("payment provider sent an invalid response") from exc
        if not isinstance(charge_id, str):
            raise PaymentFailed("payment provider sent an invalid response")
        return charge_id
