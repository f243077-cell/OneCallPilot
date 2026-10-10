"""Runs inside the short-lived helper container on chaos_net (standard library only).

The chaos CLI starts ``python:3.12.15-slim`` with this file's source as
``python -c``, the plan in ``OCP_PLAN`` (JSON list of steps) and the operator
token in ``CHAOS_TOKEN``. Nothing is published on the host and nothing executes
inside the monitored containers (A1.3 networking, option C). One CLI command makes
one helper run; the results go to stdout as one JSON object.

Steps (``op``):
- ``http``: one request; ``auth`` adds the bearer token. Result: status, body, ms.
- ``probe``: ``count`` sequential requests of one kind (``get`` or ``checkout`` =
  create a cart, then check it out). Result: list of [status, ms].
- ``ledger_read``: the ledger text ("" when it does not exist yet).
- ``ledger_append``: append whole lines in one write, then fsync.
- ``ledger_replace``: write a temporary file, fsync, rename over the ledger.
- ``sleep``: wait ``seconds``.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

LEDGER_DIR = "/ledger"
LEDGER_FILE = LEDGER_DIR + "/deploys.jsonl"
LEDGER_UID = 10001
MAX_TEXT_BODY = 200_000  # enough for a whole /metrics page
PROBE_CART = {"items": [{"product_id": 5, "quantity": 10}]}  # 150.00, a large order


def request(
    method: str, url: str, body: Any = None, token: str | None = None, timeout: float = 10.0
) -> tuple[int, Any, float]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token:
        headers["Authorization"] = "Bearer " + token
    started = time.monotonic()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc), (time.monotonic() - started) * 1000
    elapsed = (time.monotonic() - started) * 1000
    try:
        parsed: Any = json.loads(raw)
    except ValueError:
        parsed = raw.decode("utf-8", "replace")[:MAX_TEXT_BODY]
    return status, parsed, elapsed


def probe(step: dict[str, Any]) -> list[list[float]]:
    results: list[list[float]] = []
    for _ in range(int(step.get("count", 1))):
        if step["kind"] == "checkout":
            status, body, ms = request("POST", step["base"] + "/cart", PROBE_CART)
            if status == 201 and isinstance(body, dict):
                status, _, ms2 = request(
                    "POST", step["base"] + "/checkout", {"cart_id": body["cart_id"]}
                )
                ms += ms2
        else:
            status, _, ms = request("GET", step["url"])
        results.append([status, round(ms, 1)])
    return results


def _chown(path: str, uid: int, gid: int) -> None:
    # os.chown exists only on POSIX; the helper always runs on Linux.
    getattr(os, "chown")(path, uid, gid)  # noqa: B009


def _own(path: str) -> None:
    _chown(path, LEDGER_UID, LEDGER_UID)
    os.chmod(path, 0o644)


def ledger_read() -> str:
    try:
        with open(LEDGER_FILE, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def ledger_append(lines: list[str]) -> None:
    payload = "".join(line + "\n" for line in lines).encode()
    fd = os.open(LEDGER_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    _own(LEDGER_FILE)


def ledger_replace(lines: list[str]) -> None:
    temporary = LEDGER_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(line + "\n" for line in lines))
        handle.flush()
        os.fsync(handle.fileno())
    _own(temporary)
    os.replace(temporary, LEDGER_FILE)
    _chown(LEDGER_DIR, LEDGER_UID, LEDGER_UID)


def run(plan: list[dict[str, Any]], token: str) -> list[Any]:
    results: list[Any] = []
    for step in plan:
        op = step["op"]
        if op == "http":
            status, body, ms = request(
                step["method"],
                step["url"],
                step.get("json"),
                token if step.get("auth") else None,
                float(step.get("timeout", 10)),
            )
            results.append({"status": status, "body": body, "ms": round(ms, 1)})
        elif op == "probe":
            results.append(probe(step))
        elif op == "ledger_read":
            results.append(ledger_read())
        elif op == "ledger_append":
            ledger_append(step["lines"])
            results.append(None)
        elif op == "ledger_replace":
            ledger_replace(step["lines"])
            results.append(None)
        elif op == "sleep":
            time.sleep(float(step["seconds"]))
            results.append(None)
        else:
            raise ValueError("unknown step " + repr(op))
    return results


def main() -> None:
    plan = json.loads(os.environ["OCP_PLAN"])
    results = run(plan, os.environ.get("CHAOS_TOKEN", ""))
    sys.stdout.write(json.dumps({"results": results}))


if __name__ == "__main__":
    main()
