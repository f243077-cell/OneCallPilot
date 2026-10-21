"""Local signing keys and a JWKS for auth tests (never real Supabase keys)."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

ISSUER = "https://example.supabase.co/auth/v1"


class SigningKey:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())

    def jwk(self) -> dict[str, Any]:
        public: dict[str, Any] = json.loads(
            jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key())
        )
        public.update({"kid": self.kid, "alg": "ES256", "use": "sig"})
        return public

    def token(self, **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "iss": ISSUER,
            "iat": now,
            "exp": now + 3600,
            "role": "authenticated",
        }
        claims.update(overrides)
        for key in [k for k, v in claims.items() if v is None]:
            del claims[key]
        return jwt.encode(claims, self.private, algorithm="ES256", headers={"kid": self.kid})


class FakeJwks:
    """Serves a JWKS from memory and counts fetches."""

    def __init__(self, *keys: SigningKey) -> None:
        self.keys = list(keys)
        self.fetches = 0
        self.fail = False

    async def __call__(self) -> dict[str, Any]:
        from oncallpilot.api.auth import JwksUnavailableError

        self.fetches += 1
        if self.fail:
            raise JwksUnavailableError("down")
        return {"keys": [key.jwk() for key in self.keys]}
