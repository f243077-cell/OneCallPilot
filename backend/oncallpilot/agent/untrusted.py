"""Wrap tool output as untrusted data for the model (architecture §5.3, §7.7; AI-011).

The model sees tool output only inside ``<untrusted_data ref=… source=…>``.
Any closing tag (or opening tag) inside the content is escaped, so text from
logs or runbooks can never end the block early and pose as instructions.
"""

from __future__ import annotations

import re

_TAG = re.compile(r"<(/?)(untrusted_data)", re.IGNORECASE)
_ATTR = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def escape(content: str) -> str:
    return _TAG.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}", content)


def wrap(ref: str, source: str, content: str) -> str:
    if not _ATTR.match(ref) or not _ATTR.match(source):
        raise ValueError("ref and source must be short identifiers")
    return f'<untrusted_data ref="{ref}" source="{source}">\n{escape(content)}\n</untrusted_data>'
