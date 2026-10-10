"""Local screenshot links for HTML reports; no filesystem or network access."""

from __future__ import annotations

import re
from html import escape
from pathlib import PureWindowsPath
from urllib.parse import quote


def screenshot_html(path: object) -> str:
    """Link unambiguous local paths; preserve other values as escaped text."""
    if not isinstance(path, str) or not path:
        return ""

    target = ""
    normalized = path.replace("\\", "/")
    if (
        path == path.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in path)
        and not path.startswith("\\")
        and not normalized.startswith("//")
    ):
        try:
            if re.match(r"^[A-Za-z]:[/\\]", path):
                target = PureWindowsPath(path).as_uri()
            elif not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path):
                target = quote(normalized, safe="/")
        except UnicodeEncodeError:
            pass

    if target:
        return f'<br><a href="{escape(target, quote=True)}">screenshot</a>'
    return f"<br>screenshot: {escape(path, quote=True)}"
