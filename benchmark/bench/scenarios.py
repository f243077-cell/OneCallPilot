"""The 8 benchmark scenarios (BENCH-001; ``benchmark/scenarios.yaml``).

Every scenario names an expected catalogue action with its parameters, or
``escalate: true`` (scenarios 7 and 8: escalating is the correct outcome).
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from oncallpilot_contracts.enums import EnabledAction, RootCauseCategory
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENARIOS_FILE = Path(__file__).resolve().parents[1] / "scenarios.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedAction(_Strict):
    action: EnabledAction
    params: dict[str, str | int]


class Scenario(_Strict):
    id: int = Field(ge=1, le=8)
    name: str = Field(pattern=r"^[a-z]+(-[a-z]+)*$")
    inject_command: str = Field(pattern=r"^chaos inject [a-z-]+$")
    accepted_categories: list[RootCauseCategory] = Field(min_length=1)
    root_cause_keywords: list[str] = Field(min_length=1)
    expected_action: ExpectedAction | None = None
    escalate: bool = False
    timeout_minutes: int = Field(ge=1, le=60)

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if (self.expected_action is None) == (not self.escalate):
            raise ValueError("a scenario has exactly one of expected_action or escalate: true")
        if not self.inject_command.endswith(f" {self.name}"):
            raise ValueError("inject_command must inject the scenario's own name")
        return self

    @property
    def expected(self) -> str:
        """``action(k=v, …)`` or ``escalate``, as written in the CSV."""
        if self.expected_action is None:
            return "escalate"
        params = ", ".join(f"{k}={v}" for k, v in sorted(self.expected_action.params.items()))
        return f"{self.expected_action.action}({params})"


class ScenarioFile(_Strict):
    scenarios: list[Scenario] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def _ids(self) -> Self:
        if [s.id for s in self.scenarios] != list(range(1, 9)):
            raise ValueError("scenarios must be ids 1..8 in order")
        return self


def load(path: Path = SCENARIOS_FILE) -> list[Scenario]:
    return ScenarioFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).scenarios
