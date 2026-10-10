"""Who this process is: the C7 base labels (service, release, instance) and the commit."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(Exception):
    """A required setting is missing or invalid. The service refuses to start."""


@dataclass(frozen=True)
class Identity:
    service: str
    release: str
    instance: str
    # First 12 characters of the release's commit_sha (C7 `app_info{commit}`);
    # empty for services that are not in the deploy ledger.
    commit: str = ""


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"environment variable {name} is not set")
    return value


def commit_for(releases_file: Path, service: str, release: str) -> str:
    """Short commit of ``release`` from ``chaos-shop/releases.yaml``."""
    data = yaml.safe_load(releases_file.read_text(encoding="utf-8"))
    try:
        sha = data["releases"][service][release]["commit_sha"]
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"{service} {release} is not listed in {releases_file.name}") from exc
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ConfigError(f"{service} {release} has an invalid commit_sha")
    return sha[:12]


def load_identity(service: str, *, with_commit: bool) -> Identity:
    """Read ``CS_RELEASE`` (baked into the image) and ``CS_INSTANCE`` (the container name)."""
    release = require_env("CS_RELEASE")
    instance = require_env("CS_INSTANCE")
    commit = ""
    if with_commit:
        releases_file = Path(os.environ.get("CS_RELEASES_FILE", "releases.yaml"))
        commit = commit_for(releases_file, service, release)
    return Identity(service=service, release=release, instance=instance, commit=commit)
