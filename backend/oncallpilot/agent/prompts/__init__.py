"""Versioned prompts (architecture §6.7; ``CLAUDE.md`` §5.8).

``prompts/v{N}/`` holds ``system.md``, ``gather.md``, ``hypothesize.md``,
``disproof.md``, ``reflect.md`` and ``propose.md``. ``manifest.json`` stores a
SHA-256 per file. Loading a version checks every hash, so a prompt that was
edited without updating the manifest is never used; CI also refuses edits to
a version that already exists on ``main`` (a change needs a new version).
"""

from __future__ import annotations

import hashlib
import json
from functools import cached_property
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent
FILES = ("system.md", "gather.md", "hypothesize.md", "disproof.md", "reflect.md", "propose.md")


class PromptIntegrityError(Exception):
    """A prompt file is missing or does not match ``manifest.json``."""


def manifest() -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = json.loads((PROMPTS_DIR / "manifest.json").read_text())
    return data


class FilePrompts:
    """The prompts of one version, verified against the manifest."""

    def __init__(self, version: str) -> None:
        expected = manifest().get(version)
        if expected is None:
            raise PromptIntegrityError(f"prompt version {version!r} is not in manifest.json")
        if set(expected) != set(FILES):
            raise PromptIntegrityError(f"manifest for {version} must list exactly {FILES}")
        self.version = version
        self._texts: dict[str, str] = {}
        for name in FILES:
            raw = (PROMPTS_DIR / version / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected[name]:
                raise PromptIntegrityError(f"{version}/{name} does not match manifest.json")
            self._texts[name] = raw.decode("utf-8").strip()

    @cached_property
    def hash(self) -> str:
        """One hash for the whole version (part of agent_config_hash)."""
        joined = json.dumps(manifest()[self.version], sort_keys=True).encode()
        return hashlib.sha256(joined).hexdigest()

    def system(self) -> str:
        return self._texts["system.md"]

    def gather(self) -> str:
        return self._texts["gather.md"]

    def hypothesize(self) -> str:
        return self._texts["hypothesize.md"]

    def disproof(self) -> str:
        return self._texts["disproof.md"]

    def reflect(self) -> str:
        return self._texts["reflect.md"]

    def propose(self) -> str:
        return self._texts["propose.md"]
