"""FCM transport and push mapping (C9; FR-018, SEC-009). CI never calls FCM."""

from __future__ import annotations

import json
import uuid
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from oncallpilot_contracts.push import PushPayload

from oncallpilot.push.fcm import FakePushTransport, FcmTransport, ServiceAccount, message
from oncallpilot.push.notifier import push_type

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
ACCOUNT = ServiceAccount("ocp-test", "push@ocp-test.iam.example", PEM, "https://oauth.test/token")
PAYLOAD = PushPayload(
    type="proposal.created",
    incident_id=uuid.UUID("6f1c2a4e-0b7d-4c1e-9a53-2d8e4f6a7b01"),
    severity="sev1",
    service="api",
    title="api error rate 31%",
)


def test_message_has_exactly_the_five_string_data_keys() -> None:
    body = message("device-token", PAYLOAD)["message"]
    assert set(body["data"]) == {"type", "incident_id", "severity", "service", "title"}
    assert all(isinstance(v, str) for v in body["data"].values())
    assert body["notification"] == {
        "title": "api error rate 31%",
        "body": "Action needs your approval",
    }
    assert body["android"] == {
        "priority": "HIGH",
        "notification": {"channel_id": "incidents_critical"},
    }
    resolved = message("t", PAYLOAD.model_copy(update={"type": "incident.resolved"}))
    assert resolved["message"]["android"]["priority"] == "NORMAL"


class Fcm:
    def __init__(self, send_status: int = 200, send_text: str = "{}") -> None:
        self.send_status = send_status
        self.send_text = send_text
        self.token_calls = 0
        self.sent: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth.test":
            self.token_calls += 1
            form = parse_qs(request.content.decode())
            claims = jwt.decode(
                form["assertion"][0], KEY.public_key(), algorithms=["RS256"],
                audience="https://oauth.test/token",
            )  # fmt: skip
            assert claims["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
            return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600})
        self.sent.append(request)
        return httpx.Response(self.send_status, text=self.send_text)


async def test_sends_with_a_cached_access_token() -> None:
    fcm = Fcm()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fcm.handler)) as http:
        transport = FcmTransport(http, ACCOUNT)
        assert await transport.send("tok-1", PAYLOAD) == "sent"
        assert await transport.send("tok-2", PAYLOAD) == "sent"
    assert fcm.token_calls == 1
    request = fcm.sent[0]
    assert request.url.path == "/v1/projects/ocp-test/messages:send"
    assert request.headers["Authorization"] == "Bearer at-1"
    assert json.loads(request.content)["message"]["token"] == "tok-1"


async def test_unregistered_and_failed_answers() -> None:
    for status, text, expected in (
        (404, '{"error": {"status": "NOT_FOUND"}}', "unregistered"),
        (400, '{"error": {"details": [{"errorCode": "UNREGISTERED"}]}}', "unregistered"),
        (500, "oops", "failed"),
    ):
        fcm = Fcm(status, text)
        async with httpx.AsyncClient(transport=httpx.MockTransport(fcm.handler)) as http:
            assert await FcmTransport(http, ACCOUNT).send("tok", PAYLOAD) == expected


async def test_network_error_is_a_failed_push() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        assert await FcmTransport(http, ACCOUNT).send("tok", PAYLOAD) == "failed"


async def test_fake_transport_records_and_rejects() -> None:
    fake = FakePushTransport(unregistered={"dead"})
    assert await fake.send("dead", PAYLOAD) == "unregistered"
    assert await fake.send("live", PAYLOAD) == "sent"
    assert len(fake.sent) == 1


def test_event_to_push_type() -> None:
    assert push_type("incident.opened", {}) == "incident.opened"
    assert push_type("proposal.created", {}) == "proposal.created"
    assert push_type("incident.resolved", {}) == "incident.resolved"
    assert push_type("incident.updated", {"status": "escalated"}) == "incident.escalated"
    assert push_type("incident.updated", {"status": "action_failed"}) == "incident.action_failed"
    assert push_type("incident.updated", {"status": "verifying"}) is None
    assert push_type("evidence.added", {}) is None
    assert push_type("proposal.updated", {"status": "approved"}) is None
