"""The backend API as the test user (BENCH-004 step 1: leftovers are resolved by hand).

Signs in with the Supabase password grant (``OCP_TEST_EMAIL`` /
``OCP_TEST_PASSWORD``; the anon key is public by design) and sends the token
only in the ``Authorization`` header.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT_SECONDS = 15.0


class ApiError(RuntimeError):
    pass


class Api:
    def __init__(
        self,
        http: httpx.Client,
        api_url: str,
        supabase_url: str,
        anon_key: str,
        email: str,
        password: str,
    ) -> None:
        self._http = http
        self._api = api_url.rstrip("/")
        self._supabase = supabase_url.rstrip("/")
        self._anon = anon_key
        self._email = email
        self._password = password
        self._token: str | None = None

    def sign_in(self) -> None:
        response = self._http.post(
            f"{self._supabase}/auth/v1/token",
            params={"grant_type": "password"},
            headers={"apikey": self._anon},
            json={"email": self._email, "password": self._password},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            raise ApiError(f"test-user sign-in failed: HTTP {response.status_code}")
        self._token = str(response.json()["access_token"])

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self.sign_in()
        return {"Authorization": f"Bearer {self._token}"}

    def open_incidents(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            response = self._http.get(
                f"{self._api}/incidents", params=params, headers=self._headers(),
                timeout=TIMEOUT_SECONDS,
            )  # fmt: skip
            if response.status_code != 200:
                raise ApiError(f"GET /incidents failed: HTTP {response.status_code}")
            page = response.json()
            found += [i for i in page["items"] if i["status"] != "resolved"]
            cursor = page.get("next_cursor")
            if not cursor:
                return found

    def resolve(self, incident_id: str, reason: str) -> str:
        """``resolved``, ``busy`` (409 INCIDENT_BUSY) or raises."""
        response = self._http.post(
            f"{self._api}/incidents/{incident_id}/resolve", json={"reason": reason},
            headers=self._headers(), timeout=TIMEOUT_SECONDS,
        )  # fmt: skip
        if response.status_code == 200:
            return "resolved"
        if response.status_code == 409:
            return "busy"
        raise ApiError(f"resolve failed: HTTP {response.status_code}")
