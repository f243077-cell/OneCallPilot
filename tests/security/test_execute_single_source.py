"""SEC-001: an ``execute`` request is built in exactly one backend module, the approve
handler's ``approvals/execute.py``; backend-worker and the agent never build one."""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend" / "oncallpilot"
EXECUTE = re.compile(r"""["']type["']\s*:\s*["']execute["']|type\s*=\s*["']execute["']""")


def test_execute_is_constructed_in_exactly_one_module() -> None:
    builders = sorted(
        str(path.relative_to(BACKEND))
        for path in BACKEND.rglob("*.py")
        if EXECUTE.search(path.read_text(encoding="utf-8"))
    )
    assert builders == ["approvals/execute.py"]


def test_the_worker_side_never_touches_the_execute_builder_or_key() -> None:
    worker_side = [
        *(BACKEND / "agent").rglob("*.py"),
        *(BACKEND / "worker").rglob("*.py"),
        *(BACKEND / "detector").rglob("*.py"),
        BACKEND / "runner_link.py",
    ]
    for path in worker_side:
        text = path.read_text(encoding="utf-8")
        assert "approvals" not in text and "action_signing_key" not in text, path
