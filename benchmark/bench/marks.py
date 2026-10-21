"""Observer marks for the run in progress (BENCH-007): ``bench mark …``.

``results/current-run`` names the run ``bench run`` is driving; marks go to
``results/marks-<run_id>.json`` with the UTC time they were made.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.results import iso, parse_time

RESULTS = Path(__file__).resolve().parents[1] / "results"


def _now() -> datetime:
    return datetime.now(UTC)


class Marks:
    def __init__(self, folder: Path = RESULTS, clock: Callable[[], datetime] = _now) -> None:
        self.folder = folder
        self.clock = clock

    @property
    def _pointer(self) -> Path:
        return self.folder / "current-run"

    def start(self, run_id: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._pointer.write_text(run_id, encoding="utf-8")
        self._file(run_id).write_text("{}", encoding="utf-8")

    def finish(self) -> None:
        self._pointer.unlink(missing_ok=True)

    def current(self) -> str | None:
        return self._pointer.read_text(encoding="utf-8").strip() if self._pointer.exists() else None

    def _file(self, run_id: str) -> Path:
        return self.folder / f"marks-{run_id}.json"

    def mark(self, kind: str, text: str | None = None) -> dict[str, Any]:
        """``diagnosis TEXT``, ``decision`` (manual mode: the fix started), ``note TEXT``,
        ``action-correct true|false`` (manual mode)."""
        run_id = self.current()
        if run_id is None:
            raise ValueError("no run in progress (bench run starts one)")
        data: dict[str, Any] = json.loads(self._file(run_id).read_text(encoding="utf-8"))
        now = iso(self.clock())
        if kind == "diagnosis":
            if not text:
                raise ValueError("bench mark diagnosis needs the stated root cause")
            data.update(t_diagnosis=now, diagnosis_text=text)
        elif kind == "decision":
            data["t_decision"] = now
        elif kind == "note":
            data["notes"] = "; ".join(filter(None, [data.get("notes"), text]))
        elif kind == "action-correct":
            if text not in ("true", "false"):
                raise ValueError("bench mark action-correct takes true or false")
            data["action_correct"] = text == "true"
        else:
            raise ValueError(f"unknown mark {kind!r}")
        self._file(run_id).write_text(json.dumps(data, indent=1), encoding="utf-8")
        return data

    def read(self, run_id: str) -> dict[str, Any]:
        path = self._file(run_id)
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for key in ("t_diagnosis", "t_decision"):
            if key in data:
                data[key] = parse_time(data[key])
        return data
