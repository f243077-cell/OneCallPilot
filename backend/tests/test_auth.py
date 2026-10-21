"""JWT verification (SEC-010): expired, wrong audience, unknown kid, tampered payload."""

from __future__ import annotations

import base64
import json
import time
import uuid

import jwt
import pytest

from oncallpilot.api.auth import JwksCache, TokenVerifier
from oncallpilot.api.errors import UnauthorizedError
from tests.keys import ISSUER, FakeJwks, SigningKey


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(*keys: SigningKey) -> tuple[TokenVerifier, FakeJwks, Clock]:
    source = FakeJwks(*keys)
    clock = Clock()
    return TokenVerifier(JwksCache(source, clock=clock), ISSUER), source, clock


async def test_valid_token_gives_the_user() -> None:
    key = SigningKey("k1")
    verifier, _, _ = make(key)
    user_id = uuid.uuid4()
    user = await verifier.verify(key.token(sub=str(user_id)))
    assert user.user_id == user_id


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": int(time.time()) - 120},
        {"aud": "anon"},
        {"iss": "https://other.supabase.co/auth/v1"},
        {"sub": None},
        {"sub": "not-a-uuid"},
        {"exp": None},
        {"aud": None},
        {"iss": None},
    ],
)
async def test_bad_claims_are_refused(claims: dict[str, object]) -> None:
    key = SigningKey("k1")
    verifier, _, _ = make(key)
    with pytest.raises(UnauthorizedError):
        await verifier.verify(key.token(**claims))


async def test_tampered_payload_is_refused() -> None:
    key = SigningKey("k1")
    verifier, _, _ = make(key)
    header, payload, signature = key.token().split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = str(uuid.uuid4())
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(UnauthorizedError):
        await verifier.verify(f"{header}.{forged}.{signature}")


async def test_token_signed_by_an_unknown_key_is_refused() -> None:
    verifier, _, _ = make(SigningKey("k1"))
    with pytest.raises(UnauthorizedError):
        await verifier.verify(SigningKey("k1").token())  # same kid, other key


async def test_unknown_kid_refetches_once_then_refuses() -> None:
    key = SigningKey("k1")
    verifier, source, clock = make(key)
    await verifier.verify(key.token())
    assert source.fetches == 1
    clock.now += 31
    with pytest.raises(UnauthorizedError):
        await verifier.verify(SigningKey("k2").token())
    assert source.fetches == 2
    # Throttled: another unknown kid right away does not refetch.
    with pytest.raises(UnauthorizedError):
        await verifier.verify(SigningKey("k3").token())
    assert source.fetches == 2


async def test_rotated_key_is_picked_up_on_unknown_kid() -> None:
    old, new = SigningKey("old"), SigningKey("new")
    verifier, source, clock = make(old)
    await verifier.verify(old.token())
    source.keys.append(new)
    clock.now += 31
    await verifier.verify(new.token())
    assert source.fetches == 2


async def test_jwks_is_cached_for_ten_minutes() -> None:
    key = SigningKey("k1")
    verifier, source, clock = make(key)
    for _ in range(3):
        await verifier.verify(key.token())
    assert source.fetches == 1
    clock.now += 601
    await verifier.verify(key.token())
    assert source.fetches == 2


async def test_hs256_and_none_are_refused() -> None:
    key = SigningKey("k1")
    verifier, _, _ = make(key)
    claims = {"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": ISSUER, "exp": 2**31}
    hs = jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(UnauthorizedError):
        await verifier.verify(hs)
    unsigned = jwt.encode(claims, "", algorithm="none", headers={"kid": "k1"})
    with pytest.raises(UnauthorizedError):
        await verifier.verify(unsigned)


async def test_symmetric_keys_in_the_jwks_are_ignored() -> None:
    async def jwks() -> dict[str, object]:
        return {
            "keys": [
                {
                    "kty": "oct",
                    "kid": "k1",
                    "alg": "HS256",
                    "k": "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg",
                }
            ]
        }

    verifier = TokenVerifier(JwksCache(jwks), ISSUER)
    claims = {"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": ISSUER, "exp": 2**31}
    token = jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token)


async def test_jwks_outage_fails_closed() -> None:
    key = SigningKey("k1")
    verifier, source, _ = make(key)
    source.fail = True
    with pytest.raises(UnauthorizedError):
        await verifier.verify(key.token())


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "x" * 50])
async def test_garbage_is_refused(token: str) -> None:
    verifier, _, _ = make(SigningKey("k1"))
    with pytest.raises(UnauthorizedError):
        await verifier.verify(token)
