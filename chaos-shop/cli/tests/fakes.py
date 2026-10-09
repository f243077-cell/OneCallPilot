"""An in-memory Docker for the chaos CLI tests."""

import json
from dataclasses import replace
from typing import Any

from chaos_cli.config import ALL_CONTAINERS, BASELINE_RUNNING
from chaos_cli.docker_ops import ContainerState


class FakeDocker:
    def __init__(self) -> None:
        self.containers: dict[str, ContainerState] = {}
        for name in ALL_CONTAINERS:
            running = name in BASELINE_RUNNING
            self.containers[name] = ContainerState(
                name=name,
                exists=True,
                status="running" if running else "created",
                health="healthy" if running else "",
            )
        self.actions: list[tuple[str, str]] = []
        self.plans: list[list[dict[str, Any]]] = []
        self.ledger = ""
        self.responses: dict[str, Any] = {}
        self.created_slots = False

    def state(self, name: str) -> ContainerState:
        return self.containers.get(name, ContainerState(name=name, exists=False))

    def states(self, names: Any) -> dict[str, ContainerState]:
        return {name: self.state(name) for name in names}

    def _set(self, name: str, status: str, health: str) -> None:
        self.containers[name] = replace(self.containers[name], status=status, health=health)

    def start(self, names: Any) -> None:
        for name in names:
            self.actions.append(("start", name))
            self._set(name, "running", "healthy")

    def stop(self, names: Any) -> None:
        for name in names:
            self.actions.append(("stop", name))
            self._set(name, "exited", "")

    def restart(self, names: Any) -> None:
        for name in names:
            self.actions.append(("restart", name))
            self._set(name, "running", "healthy")

    def wait_healthy(self, names: Any, timeout: float) -> float:
        return 0.0

    def compose_create_slots(self) -> None:
        self.created_slots = True

    def run_helper(self, plan: list[dict[str, Any]], token: str) -> list[Any]:
        assert token == "t" * 32
        self.plans.append(plan)
        results: list[Any] = []
        for step in plan:
            if step["op"] == "ledger_read":
                results.append(self.ledger)
            elif step["op"] == "ledger_append":
                self.ledger += "".join(line + "\n" for line in step["lines"])
                results.append(None)
            elif step["op"] == "ledger_replace":
                self.ledger = "".join(line + "\n" for line in step["lines"])
                results.append(None)
            elif step["op"] == "http":
                body = self.responses.get(step["url"], {"enabled": False})
                results.append({"status": 200, "body": json.loads(json.dumps(body)), "ms": 1})
            else:
                results.append(None)
        return results

    def http_steps(self) -> list[dict[str, Any]]:
        return [step for plan in self.plans for step in plan if step["op"] == "http"]
