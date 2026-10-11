"""A2.4a end to end: a signed rollback_deploy executed by the real runner image (live).

The stack (tests/live/stack.py) runs `chaos inject 2`: api 1.5.0, whose large
checkouts fail, replaces 1.4.0. The test then does what the backend will do:

1. a signed dry run, for the state fingerprint;
2. a signed execute carrying it, published with the `publish_signed.py` CLI,
   twice (`--repeat 2`: the replay must do nothing);
3. it waits for the final result and checks the signed progress steps, the
   structural health check, the containers, a large checkout through cs-lb
   (failing before, working after), and the runner's C6 ledger record,
   validated with the shared contract model (chaos CLI environment).

The deploy ledger is mounted READ-WRITE here, as the runner service will mount
it. `chaos reset` restores everything at the end.
"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import redis

from oncallpilot_runner.signing import Keys, verify
from tests.live.stack import ROOT, ledger_text, run, runner_stack
from tests.live.test_live_runner import docker
from tests.publish_signed import build_request, entry, signed_for, vector_keys, vector_path

pytestmark = pytest.mark.live

REQUESTS, RESULTS = "ocp:runner:requests", "ocp:runner:results"
SHOP = "http://127.0.0.1:8080"
LARGE_CART = {"items": [{"product_id": 5, "quantity": 10}]}  # 150.00: fails on 1.5.0


@pytest.fixture(scope="module")
def keys() -> Keys:
    path = vector_path()
    if path is None:
        pytest.skip("signing vector not found (set OCP_CONTRACTS_DIR)")
    return vector_keys(path)


@pytest.fixture(scope="module")
def stack(keys: Keys) -> Iterator[dict[str, Any]]:
    with runner_stack(keys, ledger_writable=True) as live:
        yield live


def post(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    request = urllib.request.Request(
        f"{SHOP}{path}", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )  # fmt: skip
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def large_checkout() -> int:
    status, cart = post("/cart", LARGE_CART)
    assert status == 201, status
    status, _ = post("/checkout", {"cart_id": cart["cart_id"]})
    return status


def answers(backend: redis.Redis, key: str, value: str) -> list[dict[str, Any]]:
    rows: Any = backend.xrange(RESULTS)
    return [m for m in (json.loads(f["msg"]) for _, f in rows) if m.get(key) == value]


def wait_for(backend: redis.Redis, key: str, value: str, kind: str, seconds: int) -> Any:
    deadline = time.monotonic() + seconds
    while True:
        found = [m for m in answers(backend, key, value) if m["type"] == kind]
        if found:
            return found[0]
        assert time.monotonic() < deadline, f"no {kind} for {value}"
        time.sleep(1)


def validate_c6(line: str) -> dict[str, Any]:
    """The runner's record, checked with C6 DeployRecord itself."""
    code = (
        "import sys, json; from oncallpilot_contracts.ledger import DeployRecord; "
        "r = DeployRecord.model_validate_json(sys.argv[1]); print(r.model_dump_json())"
    )
    out = run("uv", "run", "--project", "chaos-shop/cli", "-q", "python", "-c", code, line)
    parsed: dict[str, Any] = json.loads(out)
    return parsed


def test_rollback_deploy_executes_end_to_end(stack: dict[str, Any], keys: Keys) -> None:
    backend: redis.Redis = stack["backend"]
    assert large_checkout() >= 500  # scenario 2 is live: 1.5.0 fails large orders
    ledger_before = ledger_text().splitlines()

    # 1. The dry run, as backend-worker sends it.
    params: dict[str, str | int] = {"service": "api", "target_release": "1.4.0"}
    dry = signed_for(build_request("dry_run", params=params, now=datetime.now(UTC)), keys)
    backend.xadd(REQUESTS, entry(dry))  # type: ignore[arg-type]
    preview = wait_for(backend, "request_id", dry["request_id"], "dry_run_result", 30)
    assert preview["status"] == "ok"
    fingerprint = preview["dry_run"]["state_fingerprint"]

    # 2. The execute, as backend-api sends it after approval: publish_signed.py, twice.
    out = run(
        "uv", "run", "-q", "python", "-m", "tests.publish_signed",
        "--redis", stack["backend_url"], "--type", "execute", "--action", "rollback_deploy",
        "--param", "service=api", "--param", "target_release=1.4.0",
        "--state-fingerprint", fingerprint, "--repeat", "2",
        cwd=ROOT / "runner",
    )  # fmt: skip
    first, again = (json.loads(line) for line in out.splitlines())
    execution_id = first["execution_id"]
    assert again["execution_id"] == execution_id  # the same signed message: a replay

    # 3. The final result (up to 60 s healthy + stop + 70 s structural).
    final = wait_for(backend, "execution_id", execution_id, "execution_result", 200)
    time.sleep(3)  # the replay is handled after it: nothing more may arrive
    mine = answers(backend, "execution_id", execution_id)
    steps = [m for m in mine if m["type"] == "progress"]
    assert all(verify(m, keys.link) and not verify(m, keys.action) for m in mine)
    assert [m["type"] for m in mine].count("execution_result") == 1
    assert (final["status"], final["error_code"]) == ("succeeded", None)
    assert final["captured"] == {"previous_release": "1.5.0"}
    structural = final["health_after"]["structural"]
    assert structural["passed"] is True
    assert structural["containers"] == [
        {"name": "cs-api-140-1", "state": "running", "health": "healthy"}
    ]
    assert all(p["ok"] for p in structural["probes"])
    assert [(s["step"]["name"], s["step"]["status"]) for s in steps] == [
        ("validated", "succeeded"),
        ("starting cs-api-140-1", "succeeded"),
        ("healthy cs-api-140-1", "running"),
        ("healthy cs-api-140-1", "succeeded"),
        ("stopping cs-api-150-1", "succeeded"),
        ("recording the rollback in the deploy ledger", "succeeded"),
        ("structural health check", "running"),
        ("done", "succeeded"),
    ]

    # The service recovered.
    state = {n: docker("inspect", "-f", "{{.State.Status}}", n)
             for n in ("cs-api-140-1", "cs-api-150-1")}  # fmt: skip
    assert state == {"cs-api-140-1": "running", "cs-api-150-1": "exited"}
    deadline = time.monotonic() + 15  # cs-lb drops the stopped slot within 5 s
    while large_checkout() != 201:
        assert time.monotonic() < deadline, "large checkouts still fail after the rollback"
        time.sleep(1)

    # The ledger: everything as before, plus one valid runner record.
    lines = ledger_text().splitlines()
    assert lines[:-1] == ledger_before and len(lines) == len(ledger_before) + 1
    added = validate_c6(lines[-1])
    assert (added["kind"], added["service"], added["release"], added["replicas"],
            added["deployed_by"]) == ("rollback", "api", "1.4.0", 1, "runner")  # fmt: skip

    # The replay was a duplicate: the runner logged it and did nothing.
    logs = [json.loads(x) for x in docker("logs", stack["runner"]).splitlines() if x]
    decisions = [x.get("decision") for x in logs if x.get("execution_id") == execution_id]
    assert decisions.count("executed") == 1 and decisions.count("duplicate") == 1
    used = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", stack["runner"])
    print(f"runner memory after the rollback: {used}")
