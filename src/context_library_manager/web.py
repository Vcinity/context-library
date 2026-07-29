from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.responses import HTMLResponse

ROOT = Path(__file__).parent
TEMPLATES = Environment(
    loader=FileSystemLoader(ROOT / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


def safe_href(value: str) -> str | None:
    """Return only evidence URI schemes that are safe to place in an href."""
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return None
    return value if scheme in {"http", "https", "mailto", "ticket", "evidence"} else None


TEMPLATES.filters["safe_href"] = safe_href


def assets() -> dict[str, list[str] | str | None]:
    manifest = ROOT / "static" / ".vite" / "manifest.json"
    if not manifest.is_file():
        return {"script": None, "styles": []}
    entry = json.loads(manifest.read_text(encoding="utf-8"))["frontend/main.tsx"]
    return {
        "script": "/static/" + entry["file"],
        "styles": ["/static/" + item for item in entry.get("css", [])],
    }


def render(name: str, context: dict[str, Any], status_code: int = 200) -> HTMLResponse:
    template = TEMPLATES.get_template(name)
    return HTMLResponse(template.render(**context, assets=assets()), status_code=status_code)
