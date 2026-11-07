"""ocp smoke (S2.1, TEST-014) against a fake API, a fake chaos CLI and a fake clock."""

import uuid
from pathlib import Path
from typing import Any

import pytest

from ocp_cli.commands import smoke
from ocp_cli.commands.smoke import Api, Deps, SmokeFailed, load_settings
from ocp_cli.stack import StackError

PROPOSAL = {
    "id": "p1",
    "action": "rollback_deploy",
    "params": {"service": "api", "target_release": "1.4.0"},
    "risk_tier": "medium",
    "approval_requirement": "biometric",
    "fingerprint": "f" * 64,
    "status": "pending",
}


class FakeApi:
    """The incident appears after the injection, gets a proposal on the second
    read, and is resolved two reads after the approval."""

    def __init__(self, proposal: dict[str, Any] | None = None, end: str = "resolved") -> None:
        self.proposal = dict(proposal or PROPOSAL)
        self.end = end
        self.injected = False
        self.reads = 0
        self.approved: list[tuple[str, dict[str, Any], str]] = []
        self.resolved: list[str] = []
        self.leftovers = [{"id": "old", "status": "escalated", "service": "worker"}]

    def sign_in(self) -> None:
        pass

    def incidents(self) -> list[dict[str, Any]]:
        items = [i for i in self.leftovers if i["id"] not in self.resolved]
        items += [{"id": "old", "status": "resolved", "service": "worker"}] if self.resolved else []
        if self.injected:
            items.insert(0, {"id": "i1", "status": "investigating", "service": "api",
                             "title": "api error rate 13%"})  # fmt: skip
        return items

    def incident(self, incident_id: str) -> dict[str, Any]:
        self.reads += 1
        detail: dict[str, Any] = {"id": incident_id, "status": "investigating", "proposals": [],
                                  "pending_proposal_id": None, "status_reason": None}  # fmt: skip
        if self.approved:
            after = self.reads - self.approved_at
            detail["status"] = self.end if after >= 2 else "verifying"
            detail["status_reason"] = "runner_refused" if self.end == "escalated" else None
            detail["resolution"] = "auto" if self.end == "resolved" else None
        elif self.reads >= 2:
            detail.update(status="awaiting_approval", proposals=[self.proposal],
                          pending_proposal_id=self.proposal["id"])  # fmt: skip
        return detail

    def resolve(self, incident_id: str) -> None:
        self.resolved.append(incident_id)

    def challenge(self, proposal_id: str, fingerprint: str) -> dict[str, Any]:
        assert fingerprint == self.proposal["fingerprint"]
        return {"challenge_id": "c1", "nonce": "n1", "approval_requirement": "biometric",
                "expires_at": "x"}  # fmt: skip

    def approve(self, proposal_id: str, body: dict[str, Any], key: str) -> dict[str, Any]:
        self.approved.append((proposal_id, body, key))
        self.approved_at = self.reads
        return {"execution_id": "e1", "status": "queued"}


class World:
    def __init__(self, api: FakeApi) -> None:
        self.api = api
        self.now = 0.0
        self.chaos_calls: list[tuple[str, ...]] = []
        self.said: list[str] = []
        self.sleeps: list[float] = []

    def chaos(self, *words: str) -> None:
        self.chaos_calls.append(words)
        if words[0] == "inject":
            self.api.injected = True

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def deps(self, **overrides: Any) -> Deps:
        return Deps(api=self.api, chaos=self.chaos, clock=lambda: self.now, sleep=self.sleep,
                    say=self.said.append, **overrides)  # fmt: skip


def test_the_whole_flow_resolves_after_a_real_challenge_and_approval() -> None:
    world = World(FakeApi())
    marks = smoke.smoke(world.deps())
    assert world.chaos_calls == [("reset",), ("inject", "bad-deploy")]
    assert world.sleeps[0] == 240  # warm-up before the injection
    assert world.api.resolved == ["old"]  # the leftover, before the reset
    [(proposal_id, body, key)] = world.api.approved
    assert proposal_id == "p1" and uuid.UUID(key)
    assert body == {"challenge_id": "c1", "nonce": "n1", "proposal_fingerprint": "f" * 64,
                    "auth_method": "biometric"}  # fmt: skip
    assert list(marks) == ["incident", "proposal", "approved", "resolved"]
    assert marks["resolved"] > marks["approved"] >= marks["proposal"]


def test_another_action_is_never_approved() -> None:
    world = World(FakeApi({**PROPOSAL, "action": "restart_service", "params": {"service": "api"}}))
    with pytest.raises(SmokeFailed, match="not approved"):
        smoke.smoke(world.deps())
    assert world.api.approved == []


def test_another_target_release_is_never_approved() -> None:
    params = {"service": "api", "target_release": "1.3.0"}
    world = World(FakeApi({**PROPOSAL, "params": params}))
    with pytest.raises(SmokeFailed, match="not approved"):
        smoke.smoke(world.deps())
    assert world.api.approved == []


def test_an_escalation_after_the_approval_fails_with_its_reason() -> None:
    world = World(FakeApi(end="escalated"))
    with pytest.raises(SmokeFailed, match="escalated \\(runner_refused\\)"):
        smoke.smoke(world.deps())


def test_no_resolution_within_5_minutes_of_the_approval_fails() -> None:
    world = World(FakeApi(end="verifying"))
    with pytest.raises(SmokeFailed, match="no resolution within 300 s"):
        smoke.smoke(world.deps())


def test_an_incident_opened_during_the_warm_up_stops_before_the_injection() -> None:
    # Live run, 2026-11-07: a p95_latency alert opened an api incident seconds
    # before the injection; the fault's alerts were deduplicated into it and the
    # smoke reported "no incident within 300 s".
    api = FakeApi()
    world = World(api)
    noisy = {"id": "w1", "status": "investigating", "service": "api",
             "title": "api p95 latency 389 ms"}  # fmt: skip

    def chaos(*words: str) -> None:
        world.chaos_calls.append(words)
        api.leftovers.append(noisy)  # the detector opens it during the warm-up

    world.chaos = chaos  # type: ignore[method-assign]
    with pytest.raises(SmokeFailed, match="w1 for api opened during the warm-up"):
        smoke.smoke(world.deps())
    assert world.chaos_calls == [("reset",)] and api.approved == []


def test_no_incident_fails_without_approving() -> None:
    api = FakeApi()
    world = World(api)
    world.chaos = lambda *words: world.chaos_calls.append(words)  # type: ignore[method-assign]
    with pytest.raises(SmokeFailed, match="no incident for api within 300 s"):
        smoke.smoke(world.deps())
    assert api.approved == []


# --- the HTTP side ----------------------------------------------------------------


class FakeHttp:
    def __init__(self, *replies: tuple[int, Any]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: Any = None) -> Any:
        self.calls.append((method, url, headers, body))
        return self.replies.pop(0)


SETTINGS = {
    "OCP_API_URL": "http://api:8000/",
    "SUPABASE_URL": "https://ref.supabase.co",
    "SUPABASE_ANON_KEY": "anon",
    "OCP_TEST_EMAIL": "t@x",
    "OCP_TEST_PASSWORD": "pw",
}


def test_the_token_travels_only_in_the_authorization_header() -> None:
    http = FakeHttp(
        (200, {"access_token": "tok-secret"}), (202, {"execution_id": "e", "status": "queued"})
    )
    api = Api(http, SETTINGS)
    api.sign_in()
    api.approve("p1", {"a": 1}, "key-1")
    sign_in, approve = http.calls
    assert sign_in[0:2] == ("POST", "https://ref.supabase.co/auth/v1/token?grant_type=password")
    assert sign_in[2] == {"apikey": "anon"} and sign_in[3] == {"email": "t@x", "password": "pw"}
    assert approve[1] == "http://api:8000/proposals/p1/approve"
    assert approve[2] == {"Authorization": "Bearer tok-secret", "Idempotency-Key": "key-1"}
    assert all("tok-secret" not in url for _, url, _, _ in http.calls)


def test_rate_limits_and_errors_are_reported_by_code() -> None:
    limited = {"error": {"code": "COOLDOWN_ACTIVE", "details": {"retry_after": 87}}}
    http = FakeHttp((429, limited), (409, {"error": {"code": "STALE_PROPOSAL"}}), (401, None))
    api = Api(http, SETTINGS)
    with pytest.raises(SmokeFailed, match="COOLDOWN_ACTIVE: retry after 87 s"):
        api.approve("p1", {}, "k")
    with pytest.raises(SmokeFailed, match="challenge: HTTP 409 STALE_PROPOSAL"):
        api.challenge("p1", "f")
    with pytest.raises(SmokeFailed, match="sign-in failed: HTTP 401"):
        api.sign_in()


def test_settings_come_from_the_environment_then_env_local_then_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SUPABASE_URL=https://a.supabase.co\nOCP_TEST_EMAIL=e\n",
                                   encoding="utf-8")  # fmt: skip
    (tmp_path / ".env.local").write_text("SUPABASE_ANON_KEY='anon'\nOCP_TEST_EMAIL=local\n"
                                         "OCP_TEST_PASSWORD=pw\n", encoding="utf-8")  # fmt: skip
    found = load_settings(tmp_path, {"OCP_TEST_PASSWORD": "from-env"})
    assert found == {"SUPABASE_URL": "https://a.supabase.co", "SUPABASE_ANON_KEY": "anon",
                     "OCP_TEST_EMAIL": "local", "OCP_TEST_PASSWORD": "from-env",
                     "OCP_API_URL": "http://127.0.0.1:8000"}  # fmt: skip
    (tmp_path / ".env.local").unlink()
    with pytest.raises(StackError, match="SUPABASE_ANON_KEY, OCP_TEST_PASSWORD"):
        load_settings(tmp_path, {})
