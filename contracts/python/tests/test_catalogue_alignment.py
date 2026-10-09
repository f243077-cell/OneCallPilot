"""C1 must agree with the C4 action catalogue (contracts/actions.yaml)."""

from pathlib import Path
from typing import Any, get_args

import yaml
from samples import proposal

from oncallpilot_contracts import domain, enums

CATALOGUE = Path(__file__).resolve().parents[2] / "actions.yaml"


def catalogue() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(CATALOGUE.read_text("utf-8"))
    return data


def test_enabled_actions_match_catalogue() -> None:
    actions = catalogue()["actions"]
    enabled = {name for name, action in actions.items() if action["enabled"]}
    assert set(get_args(enums.EnabledAction)) == enabled


def test_catalogue_tiers_follow_the_approval_rule() -> None:
    for name, action in catalogue()["actions"].items():
        expected = "tap" if action["risk_tier"] == "low" else "biometric"
        assert action["approval_requirement"] == expected, name


def test_sample_proposal_fits_its_catalogue_entry() -> None:
    model = domain.Proposal.model_validate(proposal())
    entry = catalogue()["actions"][model.action]
    assert set(model.params) == set(entry["params"])
    assert model.risk_tier == entry["risk_tier"]
    assert model.approval_requirement == entry["approval_requirement"]
    assert model.rollback_step is not None
    assert model.rollback_step.action == entry["rollback_step"]["action"]
