from __future__ import annotations

import argparse
import json

from .config import Settings
from .domain import RouteRequest
from .routing import route


def main() -> None:
    parser = argparse.ArgumentParser(prog="context-library-manager")
    sub = parser.add_subparsers(dest="command", required=True)
    health = sub.add_parser("health")
    health.add_argument("--project")
    dry = sub.add_parser("route")
    dry.add_argument(
        "operation",
        choices=["source", "observation", "candidate", "relationship", "publication"],
    )
    dry.add_argument("--semantic-field", action="append", default=[])
    args = parser.parse_args()
    settings = Settings.from_env(getattr(args, "project", None))
    if args.command == "health":
        print(json.dumps({"status": "healthy", "project": settings.project}))
    else:
        print(route(RouteRequest(operation=args.operation, semantic_fields=args.semantic_field)).model_dump_json())
