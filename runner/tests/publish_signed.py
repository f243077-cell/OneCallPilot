"""publish_signed.py: a fake backend that publishes signed runner requests (A2.1).

Builds C5 requests, signs them with the TEST-ONLY keys of the shared vector
(contracts/fixtures/signing/vectors.json), and adds them to
ocp:runner:requests. The tests use its functions; the CLI drives a live runner:

    uv run python -m tests.publish_signed --redis redis://backend:pw@localhost:6379/0 \\
        --vector <contracts>/fixtures/signing/vectors.json \\
        --type execute --action rollback_deploy --param service=api --param target_release=1.4.0

    --wrong-key   sign an execute with RUNNER_LINK_KEY (or a dry_run with the action key)
    --tamper      change a parameter after signing
    --expired     issue it 61 s in the past
    --repeat N    publish the same signed message N times (replay)
    --print       print the JSON instead of publishing

The vector keys are test data. Never use them, or this script, against a real stack.
"""

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from oncallpilot_runner.signing import Keys, sign

REQUESTS_STREAM = "ocp:runner:requests"
EXECUTE_TTL = timedelta(seconds=60)
DRY_RUN_TTL = timedelta(seconds=30)


def vector_path(explicit: str | None = None) -> Path | None:
    """The shared vector: --vector, $OCP_CONTRACTS_DIR, or ../contracts from runner/."""
    if explicit:
        return Path(explicit)
    base = os.environ.get("OCP_CONTRACTS_DIR") or str(
        Path(__file__).resolve().parents[2] / "contracts"
    )
    path = Path(base) / "fixtures" / "signing" / "vectors.json"
    return path if path.is_file() else None


def vector_keys(path: Path) -> Keys:
    keys = json.loads(path.read_text(encoding="utf-8"))["keys"]
    return Keys(
        action=bytes.fromhex(keys["ACTION_SIGNING_KEY"]),
        link=bytes.fromhex(keys["RUNNER_LINK_KEY"]),
    )


def ts(when: datetime) -> str:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_request(
    kind: str = "execute",
    action: str = "rollback_deploy",
    params: dict[str, str | int] | None = None,
    *,
    now: datetime | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """An unsigned C5 request; `overrides` replace any field."""
    issued = now or datetime.now(UTC)
    execute = kind == "execute"
    message: dict[str, Any] = {
        "type": kind,
        "request_id": str(uuid.uuid4()),
        "execution_id": str(uuid.uuid4()) if execute else None,
        "proposal_id": str(uuid.uuid4()) if execute else None,
        "action": action,
        "params": dict(
            params if params is not None else {"service": "api", "target_release": "1.4.0"}
        ),
        "state_fingerprint": "b" * 64 if execute else None,
        "idempotency_key": str(uuid.uuid4()) if execute else None,
        "approved_by": str(uuid.uuid4()) if execute else None,
        "approved_at": ts(issued) if execute else None,
        "issued_at": ts(issued),
        "expires_at": ts(issued + (EXECUTE_TTL if execute else DRY_RUN_TTL)),
        "catalogue_version": "1.0.0",
    }
    message.update(overrides)
    return message


def signed(message: dict[str, Any], key: bytes) -> dict[str, Any]:
    return {**message, "sig": sign(message, key)}


def signed_for(message: dict[str, Any], keys: Keys, *, wrong_key: bool = False) -> dict[str, Any]:
    """Signed with the key for its type, or deliberately with the other one."""
    right = keys.action if message["type"] == "execute" else keys.link
    wrong = keys.link if message["type"] == "execute" else keys.action
    return signed(message, wrong if wrong_key else right)


def entry(message: dict[str, Any]) -> dict[str, str]:
    """C5 transport: one field, `msg`, holding compact JSON."""
    return {"msg": json.dumps(message, separators=(",", ":"), ensure_ascii=False)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publish_signed", description=__doc__.split("\n")[0])
    parser.add_argument("--redis", help="Redis URL of a user allowed to XADD ocp:runner:requests")
    parser.add_argument("--vector", help="path of contracts/fixtures/signing/vectors.json")
    parser.add_argument("--type", default="execute", choices=["execute", "dry_run"])
    parser.add_argument("--action", default="rollback_deploy")
    parser.add_argument("--param", action="append", default=[], help="name=value (repeatable)")
    parser.add_argument("--wrong-key", action="store_true")
    parser.add_argument("--tamper", action="store_true")
    parser.add_argument("--expired", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--print", action="store_true", dest="print_only")
    args = parser.parse_args(argv)

    path = vector_path(args.vector)
    if path is None:
        print("publish_signed: the signing vector was not found (use --vector)", file=sys.stderr)
        return 2
    params: dict[str, str | int] = {}
    for item in args.param:
        name, _, value = item.partition("=")
        params[name] = int(value) if value.lstrip("-").isdigit() else value
    now = datetime.now(UTC) - (timedelta(seconds=61) if args.expired else timedelta())
    message = signed_for(
        build_request(args.type, args.action, params or None, now=now),
        vector_keys(path),
        wrong_key=args.wrong_key,
    )
    if args.tamper:
        first = next(iter(message["params"]))
        message["params"][first] = f"{message['params'][first]}-changed"
    if args.print_only:
        print(json.dumps(message, indent=2, ensure_ascii=False))
        return 0
    if not args.redis:
        print("publish_signed: --redis is required unless --print", file=sys.stderr)
        return 2
    import redis

    client = redis.Redis.from_url(args.redis, decode_responses=True, socket_connect_timeout=5)
    for _ in range(args.repeat):
        print(client.xadd(REQUESTS_STREAM, entry(message)))  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    sys.exit(main())
