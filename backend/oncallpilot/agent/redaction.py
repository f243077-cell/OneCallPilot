"""Secret redaction and the suspicious-content flag (architecture §7.7, §7.8; SEC-008, AI-028).

``redact`` runs on every tool output **before** it is stored, published, or
sent to the LLM. ``redact_payload`` walks a JSON payload: string values are
redacted, and any value under a secret-looking key (``*_KEY``, ``*_SECRET``,
``*_TOKEN``, ``*_PASSWORD``, ``password``, ``token``, …) is replaced whole.

``looks_suspicious`` is the §7.7 heuristic for instruction-like text. It only
sets ``evidence.suspicious_content``; it never changes the agent's control
flow (AI-028).

The corpus is ``tests/security/redaction_cases.yaml`` (TEST-008).
"""

from __future__ import annotations

import re
from typing import Any

# A key/variable name that holds a secret: password, passwd, pwd, secret, token,
# api_key/apikey, or anything ending in _key, _secret, _token, _password.
_SECRET_NAME = (
    r"(?:[A-Za-z0-9_]*(?:password|passwd|secret|_token|_key)|password|passwd|pwd|secret|token"
    r"|api_?key|access_?token|auth_?token|client_?secret)"
)
_SECRET_KEY = re.compile(rf"^{_SECRET_NAME}$", re.IGNORECASE)

_URL_CREDENTIALS = re.compile(
    r"\b((?:postgres(?:ql)?|redis|rediss|mysql|mariadb|mongodb(?:\+srv)?|amqps?)://)"
    r"[^\s/@]+@",
    re.IGNORECASE,
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_PROVIDER_KEYS = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}"
    r"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})"
)
# name=value (env files, query strings, command lines), also "name = value".
_ASSIGNMENT = re.compile(
    rf"\b({_SECRET_NAME})(\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s&,;'\"]+)", re.IGNORECASE
)
# "name": "value" and 'name': 'value' (JSON or Python reprs inside log text).
_QUOTED_PAIR = re.compile(
    rf"([\"']{_SECRET_NAME}[\"']\s*:\s*)(\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|[^\s,}}]+)",
    re.IGNORECASE,
)
# name: value (YAML-ish and log key: value), not when the value is a quote (handled above).
_COLON_PAIR = re.compile(rf"\b({_SECRET_NAME})(:[ \t]+)([^\s\"',;]+)", re.IGNORECASE)
_EMAIL = re.compile(
    r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
)
_BLOB = re.compile(r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=_-])")

SUSPICIOUS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)?"
        r"\s*(?:instructions|rules|prompts?)\b",
        r"\brun\s+(?:this|the\s+following)\s+command\b",
        r"\bas\s+an\s+ai\b",
        r"\bsystem\s+prompt\b",
        r"\byou\s+are\s+now\b",
        r"\bnew\s+instructions\b",
        r"\bcall\s+(?:rollback_deploy|restart_service|scale_service|clear_cache"
        r"|run_migration_rollback|propose_action)\b",
        r"\brm\s+-rf\b",
        r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba|z)?sh\b",
        r"</?untrusted_data",
    )
]


def _blob(match: re.Match[str]) -> str:
    """Long base64-looking runs, but not hex digests, UUID parts or plain paths/words."""
    token = match.group(0)
    body = token.rstrip("=")
    if re.fullmatch(r"[0-9a-fA-F]+", body):
        return token  # commit SHAs, SHA-256 digests
    mixed = (
        any(c.isdigit() for c in body)
        and any(c.isupper() for c in body)
        and any(c.islower() for c in body)
    )
    return "[REDACTED:blob]" if mixed or token.endswith("=") else token


def redact(text: str) -> str:
    text = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", text)
    text = _JWT.sub("[REDACTED:jwt]", text)
    text = _BEARER.sub("[REDACTED:bearer]", text)
    text = _PROVIDER_KEYS.sub("[REDACTED:key]", text)
    text = _QUOTED_PAIR.sub(lambda m: f'{m.group(1)}"[REDACTED:secret]"', text)
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:secret]", text)
    text = _COLON_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED:secret]", text)
    text = _EMAIL.sub("[REDACTED:email]", text)
    return _BLOB.sub(_blob, text)


def redact_payload(value: Any) -> Any:
    """A JSON value with every string redacted and secret-named keys' values replaced."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_payload(v) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY.match(str(key)) and isinstance(item, str | int | float) and item != "":
                out[key] = "[REDACTED:secret]"
            else:
                out[key] = redact_payload(item)
        return out
    return value


def looks_suspicious(value: Any) -> bool:
    """True when any string in ``value`` matches an instruction-like pattern (§7.7)."""
    if isinstance(value, str):
        return any(p.search(value) for p in SUSPICIOUS)
    if isinstance(value, list):
        return any(looks_suspicious(v) for v in value)
    if isinstance(value, dict):
        return any(looks_suspicious(v) for v in value.values())
    return False
