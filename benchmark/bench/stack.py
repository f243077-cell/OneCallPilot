"""The stack as ``bench run`` drives it: the chaos CLI, copilot-redis and git (BENCH-004).

Commands are fixed argument lists run without a shell. copilot-redis is not
published on the host, so its keys are changed through ``docker compose exec``
as the ``backend`` ACL user; the password travels in ``REDISCLI_AUTH`` (an
environment variable), never on a command line.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

LIMIT_PATTERNS = ("ocp:ratelimit:*", "ocp:cooldown:*", "ocp:runner:ratelimit:*",
                  "ocp:runner:cooldown:*")  # fmt: skip
CURRENT_RUN_KEY = "ocp:bench:current_run"


class StackError(RuntimeError):
    """A stack command failed; the run is aborted and no CSV row is written."""


class Commands(Protocol):
    def run(
        self, argv: Sequence[str], env: dict[str, str] | None = None, timeout: float = 300
    ) -> tuple[int, str]: ...


class SubprocessCommands:
    def run(
        self, argv: Sequence[str], env: dict[str, str] | None = None, timeout: float = 300
    ) -> tuple[int, str]:
        done = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(argv), capture_output=True, text=True, timeout=timeout,
            env={**os.environ, **(env or {})}, check=False,
        )  # fmt: skip
        return done.returncode, done.stdout


def redis_password(redis_url: str) -> str:
    """The ``backend`` user's password from ``REDIS_URL`` (``redis://backend:<pw>@host/0``)."""
    password = urlparse(redis_url).password
    if not password:
        raise StackError("REDIS_URL has no password")
    return password


@dataclass
class Stack:
    commands: Commands
    password: str
    chaos_cmd: list[str] = field(default_factory=lambda: ["chaos"])
    compose_cmd: list[str] = field(default_factory=lambda: ["docker", "compose"])

    def _check(self, argv: list[str], env: dict[str, str] | None = None) -> str:
        code, out = self.commands.run(argv, env)
        if code != 0:
            raise StackError(f"{' '.join(argv[:4])} … failed with exit code {code}")
        return out

    def chaos(self, *args: str) -> None:
        self._check([*self.chaos_cmd, *args])

    def redis(self, *args: str) -> str:
        argv = [*self.compose_cmd, "exec", "-T", "-e", "REDISCLI_AUTH", "copilot-redis",
                "redis-cli", "--user", "backend", "--no-auth-warning", *args]  # fmt: skip
        return self._check(argv, {"REDISCLI_AUTH": self.password})

    def clear_limits(self) -> None:
        for pattern in LIMIT_PATTERNS:
            keys = self.redis("--scan", "--pattern", pattern).split()
            if keys:
                self.redis("DEL", *keys)

    def set_current_run(self, run_id: str) -> None:
        self.redis("SET", CURRENT_RUN_KEY, run_id)

    def clear_current_run(self) -> None:
        self.redis("DEL", CURRENT_RUN_KEY)

    def agent_enabled(self) -> bool:
        out = self._check([*self.compose_cmd, "exec", "-T", "backend-api", "printenv",
                           "AGENT_ENABLED"])  # fmt: skip
        return out.strip().lower() == "true"

    def git_sha(self) -> str:
        return self._check(["git", "rev-parse", "HEAD"]).strip()
