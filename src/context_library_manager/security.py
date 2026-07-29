from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECRET_KEY = re.compile(r"(?i)(authorization|password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)")
_QUOTED_SECRET = re.compile(
    r"(?i)(\b(?:authorization|password|passwd|secret|token|api[_-]?key|credential|"
    r"private[_-]?key)\s*[:=]\s*)([\"'])(.*?)\2"
)
_UNQUOTED_SECRET = re.compile(
    r"(?i)(\b(?:authorization|password|passwd|secret|token|api[_-]?key|credential|"
    r"private[_-]?key)\s*[:=]\s*)(?:bearer\s+)?([^,;\n\r}&]+)"
)


def _structured_string(value: str) -> Any | None:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{" or stripped[-1] not in "]}":
        return None
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def redact_text(value: str, *, limit: int = 2000) -> str:
    parsed_json = _structured_string(value)
    if parsed_json is not None:
        return json.dumps(sanitize_value(parsed_json), sort_keys=True)[:limit]

    safe = _QUOTED_SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(2)}", value)
    safe = _UNQUOTED_SECRET.sub(lambda match: f"{match.group(1)}[REDACTED]", safe)
    try:
        parsed = urlsplit(safe)
        if parsed.scheme and parsed.netloc:
            hostname = parsed.hostname or ""
            netloc = hostname
            if parsed.port:
                netloc += f":{parsed.port}"
            if parsed.username or parsed.password:
                netloc = f"[REDACTED]@{netloc}"
            query = urlencode(
                [
                    (key, "[REDACTED]" if SECRET_KEY.search(key) else item)
                    for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                ]
            )
            safe = urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    except ValueError:
        pass
    return safe[:limit]


def sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEY.search(str(key)) else sanitize_value(item)
            for key, item in value.items()
        }
    return value


def filter_confidential_value(value: Any) -> Any:
    """Remove confidential fields recursively before durable/provider use."""
    if isinstance(value, str):
        parsed_json = _structured_string(value)
        if parsed_json is not None:
            filtered = filter_confidential_value(parsed_json)
            return json.dumps(filtered, sort_keys=True)
        return redact_text(value)
    if isinstance(value, list):
        return [filter_confidential_value(item) for item in value]
    if isinstance(value, tuple):
        return [filter_confidential_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): filter_confidential_value(item) for key, item in value.items() if not SECRET_KEY.search(str(key))
        }
    return value


def contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return redact_text(value, limit=max(2000, len(value) * 2)) != value
    if isinstance(value, (list, tuple)):
        return any(contains_secret(item) for item in value)
    if isinstance(value, dict):
        return any(SECRET_KEY.search(str(key)) is not None or contains_secret(item) for key, item in value.items())
    return False


def safe_error_class(exc: BaseException) -> str:
    return type(exc).__name__[:200]
