"""FCM HTTP v1 transport (C9 §1; FR-018).

The service account file (``FCM_CREDENTIALS_PATH``, a mounted secret) signs a
short-lived OAuth2 assertion (RS256) that is exchanged for an access token;
the token is cached until a minute before it expires. Requests go to
``fcm.googleapis.com`` with explicit timeouts. Device tokens, the private key
and access tokens are never logged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import jwt
from oncallpilot_contracts.push import ANDROID_CHANNEL_ID, PRIORITY, PushPayload

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TIMEOUT_SECONDS = 10.0

BODIES = {
    "incident.opened": "New incident — investigating",
    "proposal.created": "Action needs your approval",
    "incident.escalated": "Needs human investigation",
    "incident.action_failed": "Action failed — review the incident",
    "incident.resolved": "Resolved",
}

SendStatus = Literal["sent", "unregistered", "failed"]


class PushTransport(Protocol):
    async def send(self, token: str, payload: PushPayload) -> SendStatus: ...


def message(token: str, payload: PushPayload) -> dict[str, Any]:
    """The FCM v1 message: the five data keys (strings) and the notification, nothing else."""
    data = {key: str(value) for key, value in payload.model_dump(mode="json").items()}
    return {
        "message": {
            "token": token,
            "data": data,
            "notification": {"title": payload.title, "body": BODIES[payload.type]},
            "android": {
                "priority": PRIORITY[payload.type].upper(),
                "notification": {"channel_id": ANDROID_CHANNEL_ID},
            },
        }
    }


@dataclass(frozen=True)
class ServiceAccount:
    project_id: str
    client_email: str
    private_key: str
    token_uri: str

    @classmethod
    def load(cls, path: Path) -> ServiceAccount:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            str(data["project_id"]),
            str(data["client_email"]),
            str(data["private_key"]),
            str(data.get("token_uri") or "https://oauth2.googleapis.com/token"),
        )


class FcmTransport:
    def __init__(self, http: httpx.AsyncClient, account: ServiceAccount) -> None:
        self._http = http
        self._account = account
        self._token: str | None = None
        self._expires = 0.0

    async def _access_token(self) -> str:
        if self._token is not None and time.time() < self._expires - 60:
            return self._token
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self._account.client_email,
                "scope": SCOPE,
                "aud": self._account.token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            self._account.private_key,
            algorithm="RS256",
        )
        response = await self._http.post(
            self._account.token_uri,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                  "assertion": assertion},
            timeout=TIMEOUT_SECONDS,
        )  # fmt: skip
        response.raise_for_status()
        body = response.json()
        self._token = str(body["access_token"])
        self._expires = now + float(body.get("expires_in", 3600))
        return self._token

    async def send(self, token: str, payload: PushPayload) -> SendStatus:
        try:
            access = await self._access_token()
            response = await self._http.post(
                SEND_URL.format(project=self._account.project_id),
                json=message(token, payload),
                headers={"Authorization": f"Bearer {access}"},
                timeout=TIMEOUT_SECONDS,
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("push not sent: %s", type(exc).__name__)
            return "failed"
        if response.status_code == 200:
            return "sent"
        if response.status_code == 404 or "UNREGISTERED" in response.text:
            return "unregistered"
        log.warning("push refused by FCM: HTTP %d", response.status_code)
        return "failed"


class FakePushTransport:
    """Records messages; tokens in ``unregistered`` answer as FCM would (tests, CI)."""

    def __init__(self, unregistered: set[str] | None = None) -> None:
        self.unregistered = unregistered or set()
        self.sent: list[dict[str, Any]] = []

    async def send(self, token: str, payload: PushPayload) -> SendStatus:
        if token in self.unregistered:
            return "unregistered"
        self.sent.append(message(token, payload))
        return "sent"
