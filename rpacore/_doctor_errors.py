"""Privacy-bounded classification of doctor input failures."""

from __future__ import annotations

import re
import tomllib


def input_error_details(exc: Exception) -> dict[str, object]:
    """Retain only fixed causes and numeric/parser-terminal locations."""
    if isinstance(exc, tomllib.TOMLDecodeError):
        details: dict[str, object] = {"cause": "toml_syntax"}
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "colno", None)
        if type(line) is int and type(column) is int and line > 0 and column > 0:
            details["location"] = f"line {line}, column {column}"
        else:
            match = re.search(r"\(at (line [0-9]+, column [0-9]+|end of document)\)\Z", str(exc))
            if match is not None:
                details["location"] = match[1]
        return details
    if isinstance(exc, KeyError):
        cause = "invalid_shape"
    elif isinstance(exc, TypeError):
        cause = "invalid_type"
    elif isinstance(exc, ValueError):
        cause = "invalid_value"
    elif isinstance(exc, OSError):
        cause = "unreadable"
    else:
        cause = "unexpected"
    return {"cause": cause}
