"""TB-010 grep test: no agent-visible name or field names a fault or the injector.

Covers releases.yaml, runbooks/, the container names, labels and aliases in
testbed.yml, and a sample ledger written by the chaos CLI (reset plus the fault
deploys of scenarios 2 and 6 and verify's direct fixes). Two ledger fields are
left out: ``image_tag``, which C6 fixes to ``chaos-shop/<service>:<release>`` and
get_recent_deploys never returns, and ``deploy_id``, a random UUID whose hex
digits can spell "bad" by chance without naming anything.
"""

import re
from pathlib import Path
from typing import Any

import yaml

from chaos_cli import ledger
from chaos_cli.config import RELEASES_FILE, REPO_ROOT

BANNED = re.compile(r"bad|leak|broken|chaos|fault|inject", re.IGNORECASE)
TESTBED = REPO_ROOT / "infrastructure" / "compose" / "testbed.yml"
RUNBOOKS = REPO_ROOT / "runbooks"


def visible_strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in visible_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in visible_strings(v)]
    return [str(value)]


def test_releases_file_is_neutral() -> None:
    data = yaml.safe_load(RELEASES_FILE.read_text(encoding="utf-8"))
    fields = visible_strings(data["releases"]) + visible_strings(data["baseline"])
    for entry in data["history"]:
        fields += [str(v) for k, v in entry.items() if k != "image_tag"]
    assert [f for f in fields if BANNED.search(f)] == []


def test_runbooks_are_neutral() -> None:
    hits = [
        (path.name, line)
        for path in RUNBOOKS.rglob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if BANNED.search(line)
    ]
    assert hits == []


def test_container_names_labels_and_aliases_are_neutral() -> None:
    services = yaml.safe_load(TESTBED.read_text(encoding="utf-8"))["services"]
    hits = []
    for name, service in services.items():
        values = [name, service["container_name"], *map(str, service.get("labels", {}).values())]
        networks = service.get("networks", {})
        if isinstance(networks, dict):
            for config in networks.values():
                values += (config or {}).get("aliases", [])
        hits += [v for v in values if BANNED.search(v)]
    assert hits == []


def test_a_ledger_written_by_the_cli_is_neutral() -> None:
    releases = ledger.Releases.load()
    records = [
        *ledger.seeded_ledger(releases),
        ledger.make_record(releases, kind="deploy", service="api", release="1.5.0",
                           replicas=1, deployed_by="ci", reason=ledger.REASON_RELEASE),
        ledger.make_record(releases, kind="deploy", service="worker", release="2.2.0",
                           replicas=1, deployed_by="ci", reason=ledger.REASON_RELEASE),
        ledger.make_record(releases, kind="rollback", service="api", release="1.4.0",
                           replicas=1, deployed_by="setup", reason=ledger.REASON_ROLLBACK),
        ledger.make_record(releases, kind="scale", service="api", release="1.4.0",
                           replicas=3, deployed_by="setup", reason=ledger.REASON_SCALE),
    ]  # fmt: skip
    hits = [
        (key, value)
        for record in records
        for key, value in record.model_dump(mode="json").items()
        if key not in ("image_tag", "deploy_id") and BANNED.search(str(value))
    ]
    assert hits == []


def test_slots_run_the_configuration_their_ledger_hash_describes() -> None:
    """config_hash is honest: each slot's non-secret settings equal releases.yaml `config`."""
    services = yaml.safe_load(TESTBED.read_text(encoding="utf-8"))["services"]
    releases = ledger.Releases.load()
    for name, service in services.items():
        image = service.get("image", "")
        if not image.startswith(("chaos-shop/api:", "chaos-shop/worker:")):
            continue
        kind, release = image.removeprefix("chaos-shop/").split(":")
        config = releases.entry(kind, release)["config"]
        environment = service["environment"]
        assert {key: environment.get(key) for key in config} == config, name
        if kind == "worker":
            assert "QUEUE_BATCH_SIZE" in config, name


def test_logger_names_are_neutral() -> None:
    """The C7 `logger` field is agent-visible: every logger name in the services is neutral."""
    shop = REPO_ROOT / "chaos-shop"
    names = []
    for path in shop.rglob("*.py"):
        if ".venv" in path.parts or "tests" in path.parts or "cli" in path.parts:
            continue
        names += re.findall(r'getLogger\("([^"]+)"\)', path.read_text(encoding="utf-8"))
    names += re.findall(
        r'"logger":"([^"]+)"', (shop / "lb" / "nginx.conf").read_text(encoding="utf-8")
    )
    assert names, "no logger names found"
    assert [n for n in names if BANNED.search(n)] == []
    # Third-party loggers that common/jsonlog.py configures by name.
    third_party = {"uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"}
    own = [n for n in names if n not in third_party]
    assert all(n.startswith("shop.") or n == "nginx.access" for n in own), own


def test_paths_exist() -> None:
    assert Path(RELEASES_FILE).exists() and TESTBED.exists()
