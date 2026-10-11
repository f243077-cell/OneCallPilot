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

Then (A2.4b) each other action, the same way (signed dry run, then the signed
execute through publish_signed.py):
- restart_service of worker, redis and api: each container really restarted
  (new start time), running and healthy, and no ledger record;
- scale_service of api 1 -> 3 -> 1: slots started, then the highest stopped
  first, a C6-valid `scale` record each time, and the shop up throughout;
- clear_cache of catalog and pricing: the step reports the keys cleared,
  cs-api logged the clear, the structural check covers api and cs-redis, and
  no ledger record.

The deploy ledger is mounted READ-WRITE here, as the runner service will mount
it. `chaos reset` restores everything at the end of the module.

These tests run many actions on api within minutes, which the runner's own
rate limit and cooldown (A2.5) would refuse. `execute_signed` therefore clears
the runner's counters before each execute (through the test's admin Redis
user, never the runner's). The limits themselves are tested live in
test_live_limits.py.
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


# === A2.4b: restart_service, scale_service, clear_cache (after the rollback above) ======


LIMITED = ("api", "worker", "redis")


def clear_limits(backend: redis.Redis) -> None:
    keys = [f"ocp:runner:{kind}:{s}" for s in LIMITED for kind in ("ratelimit", "cooldown")]
    backend.delete(*keys)


def execute_signed(
    stack: dict[str, Any], keys: Keys, action: str, params: dict[str, str | int],
    *, fresh_limits: bool = True,
) -> tuple[dict[str, Any], list[tuple[str, str, str]]]:  # fmt: skip
    """A signed dry run for the fingerprint, then the signed execute through
    publish_signed.py; returns the verified final result and its steps.
    `fresh_limits` clears the runner's counters first (see the module docstring)."""
    backend: redis.Redis = stack["backend"]
    if fresh_limits:
        clear_limits(backend)
    dry = signed_for(build_request("dry_run", action, params, now=datetime.now(UTC)), keys)
    backend.xadd(REQUESTS, entry(dry))  # type: ignore[arg-type]
    preview = wait_for(backend, "request_id", dry["request_id"], "dry_run_result", 30)
    assert preview["status"] == "ok", preview
    args = [f"--param={k}={v}" for k, v in params.items()]
    out = run(
        "uv", "run", "-q", "python", "-m", "tests.publish_signed",
        "--redis", stack["backend_url"], "--type", "execute", "--action", action, *args,
        "--state-fingerprint", preview["dry_run"]["state_fingerprint"],
        cwd=ROOT / "runner",
    )  # fmt: skip
    execution_id = json.loads(out)["execution_id"]
    final = wait_for(backend, "execution_id", execution_id, "execution_result", 200)
    mine = answers(backend, "execution_id", execution_id)
    assert all(verify(m, keys.link) and not verify(m, keys.action) for m in mine)
    steps = [(m["step"]["name"], m["step"]["status"], m["step"]["message"])
             for m in mine if m["type"] == "progress"]  # fmt: skip
    return final, steps


def started_at(name: str) -> str:
    return docker("inspect", "-f", "{{.State.StartedAt}}", name)


def states(*names: str) -> dict[str, str]:
    return {n: docker("inspect", "-f", "{{.State.Status}}/{{.State.Health.Status}}", n)
            for n in names}  # fmt: skip


def active_api() -> tuple[str, int]:
    records = [json.loads(x) for x in ledger_text().splitlines()]
    latest = [r for r in records if r["service"] == "api"][-1]
    return latest["release"], latest["replicas"]


def slot(release: str, replica: int) -> str:
    return f"cs-api-{release.replace('.', '')}-{replica}"


def shop_up() -> int:
    with urllib.request.urlopen(f"{SHOP}/products", timeout=10) as response:
        return int(response.status)


@pytest.mark.parametrize(("service", "container"),
                         [("worker", "cs-worker-210"), ("redis", "cs-redis")])  # fmt: skip
def test_restart_service_executes_end_to_end(
    stack: dict[str, Any], keys: Keys, service: str, container: str
) -> None:
    ledger_before, before = ledger_text(), started_at(container)
    final, steps = execute_signed(stack, keys, "restart_service", {"service": service})
    assert (final["status"], final["captured"]) == ("succeeded", {}), steps
    assert final["health_after"]["structural"]["passed"] is True
    assert (f"restarting {container}", "succeeded") in [(n, s) for n, s, _ in steps]
    assert started_at(container) != before  # it really restarted
    assert states(container) == {container: "running/healthy"}
    assert ledger_text() == ledger_before  # no record for a restart


def test_restart_service_of_api_executes_end_to_end(stack: dict[str, Any], keys: Keys) -> None:
    release, replicas = active_api()
    names = [slot(release, r) for r in range(1, replicas + 1)]
    before = {n: started_at(n) for n in names}
    final, steps = execute_signed(stack, keys, "restart_service", {"service": "api"})
    assert final["status"] == "succeeded", steps
    assert all(started_at(n) != before[n] for n in names)
    assert shop_up() == 200


def test_scale_service_executes_end_to_end_up_and_down(stack: dict[str, Any], keys: Keys) -> None:
    release, replicas = active_api()
    assert replicas == 1
    up, steps = execute_signed(stack, keys, "scale_service", {"service": "api", "replicas": 3})
    assert (up["status"], up["captured"]) == ("succeeded", {"previous_replicas": 1}), steps
    three = [slot(release, r) for r in (1, 2, 3)]
    assert set(states(*three).values()) == {"running/healthy"}
    added = validate_c6(ledger_text().splitlines()[-1])
    assert (added["kind"], added["release"], added["replicas"], added["deployed_by"]) == (
        "scale", release, 3, "runner",
    )  # fmt: skip
    assert active_api() == (release, 3)

    down, steps = execute_signed(stack, keys, "scale_service", {"service": "api", "replicas": 1})
    assert (down["status"], down["captured"]) == ("succeeded", {"previous_replicas": 3}), steps
    assert [n for n, s, _ in steps if n.startswith("stopping")] == [
        f"stopping {slot(release, 3)}", f"stopping {slot(release, 2)}",
    ]  # fmt: skip
    assert states(three[0]) == {three[0]: "running/healthy"}
    assert [docker("inspect", "-f", "{{.State.Status}}", n) for n in three[1:]] == [
        "exited", "exited",
    ]  # fmt: skip
    assert active_api() == (release, 1)
    assert shop_up() == 200  # never down: slot 1 kept serving


@pytest.mark.parametrize("cache", ["catalog", "pricing"])
def test_clear_cache_executes_end_to_end(stack: dict[str, Any], keys: Keys, cache: str) -> None:
    for _ in range(3):  # warm the caches through the shop
        shop_up()
    release, _ = active_api()
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ledger_before = ledger_text()
    final, steps = execute_signed(stack, keys, "clear_cache", {"cache": cache})
    assert (final["status"], final["captured"]) == ("succeeded", {}), steps
    (message,) = [m for n, s, m in steps if n == f"clearing the {cache} cache" and s == "succeeded"]
    assert message.startswith(f"{cache}: ") and "keys cleared" in message
    structural = final["health_after"]["structural"]
    assert [c["name"] for c in structural["containers"]] == [slot(release, 1), "cs-redis"]
    logs = docker("logs", "--since", since, slot(release, 1))
    assert f"{cache} cache cleared" in logs  # cs-api logs every clear (shop.admin)
    assert ledger_text() == ledger_before
