"""ocp smoke: scenario 2 end to end through the API (S2.1, TEST-014).

    chaos reset → warm-up → chaos inject bad-deploy → the detector opens the
    incident → the agent proposes → challenge and approve as the test user →
    the incident is resolved within 5 minutes of the approval.

Approval is never skipped: the script asks for a challenge and approves with
the method the challenge names (`biometric` for rollback_deploy, as the app
sends after the fingerprint prompt; architecture §7.12 L2). It approves only
the action scenario 2 expects (rollback_deploy of api to 1.4.0); any other
proposal, or an escalation, ends the smoke without approving anything.

Reads SUPABASE_URL, SUPABASE_ANON_KEY, OCP_TEST_EMAIL, OCP_TEST_PASSWORD and
OCP_API_URL from the environment, else from .env.local, else from .env. The
token goes only in the Authorization header and is never printed.
"""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ocp_cli.stack import Stack, StackError

NAME = "smoke"
HELP = "scenario 2 end to end through the API, approved as the test user (TEST-014)"

SCENARIO = "bad-deploy"
SERVICE = "api"
EXPECTED = {"action": "rollback_deploy", "params": {"service": "api", "target_release": "1.4.0"}}
WARMUP_SECONDS = 180 + 60  # detector warm-up after a reset, plus a healthy baseline
PROPOSAL_TIMEOUT = 300.0
RESOLVE_TIMEOUT = 300.0  # TEST-014: resolved within 5 minutes of the approval
POLL_SECONDS = 3.0
HTTP_TIMEOUT = 15.0
DEFAULT_API = "http://127.0.0.1:8000"
SETTINGS = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "OCP_TEST_EMAIL", "OCP_TEST_PASSWORD")


class SmokeFailed(Exception):
    """The flow did not reach `resolved`; the message says where it stopped."""


# --- settings -----------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def load_settings(root: Path, environ: Mapping[str, str]) -> dict[str, str]:
    files = {**read_env_file(root / ".env"), **read_env_file(root / ".env.local")}
    found = {key: environ.get(key) or files.get(key, "") for key in SETTINGS}
    missing = [key for key, value in found.items() if not value]
    if missing:
        raise StackError(f"set {', '.join(missing)} (in .env.local or the environment)")
    found["OCP_API_URL"] = environ.get("OCP_API_URL") or files.get("OCP_API_URL") or DEFAULT_API
    return found


# --- HTTP -----------------------------------------------------------------------

Reply = tuple[int, Any]  # (status, JSON body or None)


class Http(Protocol):
    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: Any = None
    ) -> Reply: ...


def urllib_http(method: str, url: str, headers: dict[str, str], body: Any = None) -> Reply:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise SmokeFailed(f"{method} {url.split('?')[0]}: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def _code(body: Any) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    return str(error.get("code")) if isinstance(error, dict) else ""


class Api:
    """The backend API as the test user (password grant against Supabase)."""

    def __init__(self, http: Http, settings: Mapping[str, str]) -> None:
        self._http = http
        self._api = settings["OCP_API_URL"].rstrip("/")
        self._supabase = settings["SUPABASE_URL"].rstrip("/")
        self._anon = settings["SUPABASE_ANON_KEY"]
        self._email = settings["OCP_TEST_EMAIL"]
        self._password = settings["OCP_TEST_PASSWORD"]
        self._token = ""

    def sign_in(self) -> None:
        status, body = self._http(
            "POST",
            f"{self._supabase}/auth/v1/token?grant_type=password",
            {"apikey": self._anon},
            {"email": self._email, "password": self._password},
        )
        if status != 200 or not isinstance(body, dict) or "access_token" not in body:
            raise SmokeFailed(f"test-user sign-in failed: HTTP {status}")
        self._token = str(body["access_token"])

    def _call(self, method: str, path: str, body: Any = None, **headers: str) -> Reply:
        auth = {"Authorization": f"Bearer {self._token}", **headers}
        return self._http(method, f"{self._api}{path}", auth, body)

    def _ok(self, method: str, path: str, expect: int, body: Any = None, **headers: str) -> Any:
        status, reply = self._call(method, path, body, **headers)
        if status != expect:
            raise SmokeFailed(f"{method} {path}: HTTP {status} {_code(reply)}".rstrip())
        return reply

    def incidents(self) -> list[dict[str, Any]]:
        page = self._ok("GET", "/incidents?limit=50", 200)
        return list(page["items"])

    def incident(self, incident_id: str) -> dict[str, Any]:
        detail: dict[str, Any] = self._ok("GET", f"/incidents/{incident_id}", 200)
        return detail

    def resolve(self, incident_id: str) -> None:
        status, reply = self._call(
            "POST", f"/incidents/{incident_id}/resolve", {"reason": "smoke_cleanup"}
        )
        if status == 409:
            raise SmokeFailed(f"incident {incident_id} is busy ({_code(reply)}); try again later")
        if status != 200:
            raise SmokeFailed(f"resolving incident {incident_id}: HTTP {status} {_code(reply)}")

    def challenge(self, proposal_id: str, fingerprint: str) -> dict[str, Any]:
        issued: dict[str, Any] = self._ok(
            "POST",
            f"/proposals/{proposal_id}/challenge",
            200,
            {"proposal_fingerprint": fingerprint},
        )
        return issued

    def approve(self, proposal_id: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        status, reply = self._call(
            "POST", f"/proposals/{proposal_id}/approve", body, **{"Idempotency-Key": key}
        )
        if status == 429:
            details = reply.get("error", {}).get("details", {}) if isinstance(reply, dict) else {}
            raise SmokeFailed(
                f"approve refused {_code(reply)}: retry after {details.get('retry_after')} s"
            )
        if status != 202 or not isinstance(reply, dict):
            raise SmokeFailed(f"approve: HTTP {status} {_code(reply)}".rstrip())
        return reply


# --- the flow -------------------------------------------------------------------


class ApiLike(Protocol):
    def sign_in(self) -> None: ...
    def incidents(self) -> list[dict[str, Any]]: ...
    def incident(self, incident_id: str) -> dict[str, Any]: ...
    def resolve(self, incident_id: str) -> None: ...
    def challenge(self, proposal_id: str, fingerprint: str) -> dict[str, Any]: ...
    def approve(self, proposal_id: str, body: dict[str, Any], key: str) -> dict[str, Any]: ...


@dataclass
class Deps:
    api: ApiLike
    chaos: Callable[..., None]
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    say: Callable[[str], None] = print
    warmup: float = WARMUP_SECONDS
    proposal_timeout: float = PROPOSAL_TIMEOUT
    resolve_timeout: float = RESOLVE_TIMEOUT
    poll: float = POLL_SECONDS
    marks: dict[str, float] = field(default_factory=dict)


def _until(deps: Deps, timeout: float, what: str, check: Callable[[], Any]) -> Any:
    deadline = deps.clock() + timeout
    while True:
        found = check()
        if found is not None:
            return found
        if deps.clock() >= deadline:
            raise SmokeFailed(f"no {what} within {timeout:.0f} s")
        deps.sleep(deps.poll)


def _stopped(detail: dict[str, Any], waiting_for: str) -> None:
    if detail["status"] in ("escalated", "action_failed"):
        reason = detail.get("status_reason") or "no reason given"
        raise SmokeFailed(f"incident {detail['status']} ({reason}) while waiting for {waiting_for}")


def smoke(deps: Deps) -> dict[str, float]:
    """Runs the flow; returns seconds from the injection for each step."""
    api, say = deps.api, deps.say
    api.sign_in()
    say("signed in as the test user")
    for leftover in [i for i in api.incidents() if i["status"] != "resolved"]:
        api.resolve(str(leftover["id"]))
        say(f"resolved leftover incident {leftover['id']} ({leftover['status']})")

    say("chaos reset")
    deps.chaos("reset")
    say(f"waiting {deps.warmup:.0f} s for the detector warm-up and a healthy baseline")
    deps.sleep(deps.warmup)
    before = api.incidents()
    noisy = [i for i in before if i["service"] == SERVICE and i["status"] != "resolved"]
    if noisy:
        # The fault's alerts would be deduplicated into it: no new incident to follow.
        raise SmokeFailed(
            f"incident {noisy[0]['id']} for {SERVICE} opened during the warm-up"
            f" ({noisy[0]['title']}): the baseline was not quiet; nothing injected, run again"
        )
    known = {str(i["id"]) for i in before}

    say(f"chaos inject {SCENARIO}")
    deps.chaos("inject", SCENARIO)
    injected = deps.clock()
    marks = deps.marks

    def mark(name: str) -> float:
        marks[name] = round(deps.clock() - injected, 1)
        return marks[name]

    def new_incident() -> dict[str, Any] | None:
        fresh = [i for i in api.incidents() if str(i["id"]) not in known]
        return next((i for i in fresh if i["service"] == SERVICE), None)

    incident = _until(deps, deps.proposal_timeout, "incident for api", new_incident)
    incident_id = str(incident["id"])
    say(f"+{mark('incident')} s incident {incident_id}: {incident['title']}")

    def pending_proposal() -> dict[str, Any] | None:
        detail = api.incident(incident_id)
        _stopped(detail, "a proposal")
        if detail["status"] == "resolved":
            raise SmokeFailed("the incident was resolved before any approval")
        wanted = detail.get("pending_proposal_id")
        return next(
            (
                p
                for p in detail.get("proposals", [])
                if p["id"] == wanted and p["status"] == "pending"
            ),
            None,
        )

    remaining = deps.proposal_timeout - (deps.clock() - injected)
    proposal = _until(deps, max(remaining, 0.0), "pending proposal", pending_proposal)
    say(
        f"+{mark('proposal')} s proposal {proposal['action']} {json.dumps(proposal['params'])}"
        f" (risk {proposal['risk_tier']}, needs {proposal['approval_requirement']})"
    )
    got = {"action": proposal["action"], "params": proposal["params"]}
    if got != EXPECTED:
        raise SmokeFailed(f"not approved: scenario 2 expects {json.dumps(EXPECTED)}")

    issued = api.challenge(str(proposal["id"]), str(proposal["fingerprint"]))
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": proposal["fingerprint"],
        "auth_method": issued["approval_requirement"],
    }
    accepted = api.approve(str(proposal["id"]), body, str(uuid.uuid4()))
    approved = deps.clock()
    say(
        f"+{mark('approved')} s approved ({body['auth_method']}),"
        f" execution {accepted['execution_id']} {accepted['status']}"
    )

    def resolved() -> dict[str, Any] | None:
        detail = api.incident(incident_id)
        _stopped(detail, "resolved")
        return detail if detail["status"] == "resolved" else None

    detail = _until(deps, deps.resolve_timeout, "resolution", resolved)
    say(
        f"+{mark('resolved')} s resolved ({detail.get('resolution')}),"
        f" {deps.clock() - approved:.0f} s after the approval"
    )
    return marks


# --- command --------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--warmup", type=float, default=WARMUP_SECONDS,
        help="seconds to wait after the reset before injecting (default 240)",
    )  # fmt: skip


def run(args: argparse.Namespace, stack: Stack) -> int:
    settings = load_settings(stack.root, os.environ)

    def chaos(*words: str) -> None:
        stack.check(["uv", "run", "--project", "chaos-shop/cli", "chaos", *words], capture=False)

    deps = Deps(
        api=Api(urllib_http, settings),
        chaos=chaos,
        say=lambda text: print(f"[ocp smoke] {text}"),
        warmup=args.warmup,
    )
    try:
        smoke(deps)
    except SmokeFailed as exc:
        print(f"[ocp smoke] FAIL: {exc}")
        return 1
    print("[ocp smoke] PASS")
    return 0
