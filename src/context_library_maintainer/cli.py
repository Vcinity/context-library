from __future__ import annotations

import json as jsonlib
import os
import sys
from pathlib import Path
from typing import Optional

import typer

from context_library_core.contracts import SCHEMA_FAMILIES
from context_library_core.version import VERSION

from .config import ConfigError, resolve_config
from .ingest import envelopes_from
from .models import (
    Response,
    digest,
    safe_error,
)
from .service import MaintainerApplicationService, MaintainerContext

app = typer.Typer(no_args_is_help=True, add_completion=False)
observe_app = typer.Typer()
candidate_app = typer.Typer()
finding_app = typer.Typer()
work_app = typer.Typer()
conflict_app = typer.Typer()
migrate_app = typer.Typer()
app.add_typer(observe_app, name="observe")
app.add_typer(candidate_app, name="candidate")
app.add_typer(finding_app, name="finding")
app.add_typer(work_app, name="work")
app.add_typer(conflict_app, name="conflict")
app.add_typer(migrate_app, name="migrate")


@app.callback()
def global_options(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None, "--state-root"),
    project: Optional[str] = typer.Option(None, "--project"),
    actor: Optional[str] = typer.Option(None, "--actor"),
    json_output: bool = typer.Option(False, "--json"),
    verbose: bool = typer.Option(False, "--verbose"),
):
    if library_root:
        os.environ["CONTEXT_LIBRARY_ROOT"] = str(library_root)
    if state_root:
        os.environ["CLM_STATE_ROOT"] = str(state_root)
    if project:
        os.environ["CLM_PROJECT"] = project
    if actor:
        os.environ["CLM_ACTOR"] = actor
    if json_output:
        os.environ["CLM_JSON"] = "true"
    if verbose:
        os.environ["CLM_VERBOSE"] = "true"


def settings(
    library_root: Path | None,
    state_root: Optional[Path],
    project: Optional[str],
    actor: Optional[str],
    as_json: Optional[bool],
    auto_publish: Optional[bool],
) -> dict:
    effective_root = library_root or (
        Path(os.environ["CONTEXT_LIBRARY_ROOT"]) if os.getenv("CONTEXT_LIBRARY_ROOT") else None
    )
    if effective_root is None:
        raise ConfigError("library root is required via --library-root or CONTEXT_LIBRARY_ROOT")
    resolved = resolve_config(effective_root, project, state_root, actor, as_json, auto_publish)
    if not resolved.get("project"):
        raise ConfigError("project is required via --project or CLM_PROJECT")
    return resolved


def emit(
    command: str,
    status: str,
    run_id: str,
    data: dict | None = None,
    errors: list[dict[str, str]] | None = None,
    as_json: bool = False,
) -> None:
    response = Response(command=command, status=status, run_id=run_id, data=data or {}, errors=errors or [])
    if as_json:
        typer.echo(response.model_dump_json())
    else:
        typer.echo(jsonlib.dumps(response.data, indent=2, ensure_ascii=False))


def require_settings(
    command: str,
    library_root: Path | None,
    state_root: Optional[Path],
    project: Optional[str],
    actor: Optional[str],
    as_json: Optional[bool],
    auto_publish: Optional[bool] = None,
) -> dict:
    try:
        return settings(library_root, state_root, project, actor, as_json, auto_publish)
    except ConfigError as exc:
        json_output = as_json if as_json is not None else os.getenv("CLM_JSON", "false").lower() == "true"
        emit(
            command,
            "error",
            "run_" + __import__("uuid").uuid4().hex,
            errors=[{"code": "configuration", "message": safe_error(exc)}],
            as_json=json_output,
        )
        raise typer.Exit(2) from None


def service_for(s: dict) -> MaintainerApplicationService:
    return MaintainerApplicationService(
        MaintainerContext(
            library_root=s["library_root"],
            state_root=s["state_root"],
            project=s["project"],
            actor=s["actor"],
        )
    )


@app.command("version")
def version_cmd(json: bool = typer.Option(False, "--json")):
    emit(
        "version",
        "ok",
        "run_" + __import__("uuid").uuid4().hex,
        {
            "schema": "context-library/version",
            "schema_version": 1,
            "product_version": VERSION,
        },
        as_json=json or os.getenv("CLM_JSON", "false").lower() == "true",
    )


@app.command("capabilities")
def capabilities_cmd(json: bool = typer.Option(False, "--json")):
    emit(
        "capabilities",
        "ok",
        "run_" + __import__("uuid").uuid4().hex,
        {
            "schema": "context-library/capabilities",
            "schema_version": 1,
            "product_version": VERSION,
            "schema_families": SCHEMA_FAMILIES,
            "canonical_layout_versions": ["legacy-flat-pack", "project-pack-v1"],
            "features": {
                "typed_service": True,
                "legacy_read": True,
                "project_pack_write": True,
                "plugin_canonical_write": False,
            },
        },
        as_json=json or os.getenv("CLM_JSON", "false").lower() == "true",
    )


@app.command("query")
def query_cmd(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    project: Optional[str] = typer.Option(None),
    query: str = typer.Option("", "--q"),
    decision_id: Optional[str] = typer.Option(None),
    status: Optional[str] = typer.Option(None),
    category: Optional[str] = typer.Option(None),
    page: int = typer.Option(1, min=1),
    page_size: int = typer.Option(25, min=1, max=100),
    digest_only: bool = typer.Option(False),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("query", library_root, None, project, None, json)
    run_id = (
        "run_query_"
        + digest(
            [
                s["library_root"],
                s["project"],
                query,
                decision_id,
                status,
                category,
                page,
                page_size,
                digest_only,
            ]
        )[:20]
    )
    try:
        data = service_for(s).query(
            project=s["project"],
            query=query,
            decision_id=decision_id,
            status=status,
            category=category,
            page=page,
            page_size=page_size,
            digest_only=digest_only,
        )
        emit("query", "ok", run_id, data, as_json=s["json"])
    except KeyError as exc:
        emit(
            "query",
            "error",
            run_id,
            errors=[{"code": "decision-not-found", "message": str(exc.args[0])}],
            as_json=s["json"],
        )
        raise typer.Exit(2)
    except Exception as exc:
        emit(
            "query",
            "error",
            run_id,
            errors=[{"code": "query-error", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@app.command()
def init(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("init", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).initialize()
        emit("init", "ok", result.pop("run_id"), result, as_json=s["json"])
    except typer.Exit:
        raise
    except Exception as exc:
        emit(
            "init",
            "error",
            "run_" + __import__("uuid").uuid4().hex,
            errors=[{"code": "configuration", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@app.command("ingest")
def ingest_cmd(
    file: Optional[Path] = typer.Option(None, "--file"),
    directory: Optional[Path] = typer.Option(None, "--directory"),
    stdin: bool = typer.Option(False, "--stdin"),
    atomic: bool = typer.Option(False),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("ingest", library_root, state_root, project, actor, json)
    try:
        text = sys.stdin.read() if stdin else None
        if directory and not atomic:
            sources = []
            errors = []
            for item in sorted(directory.iterdir(), key=lambda path: path.as_posix().encode()):
                if not item.is_file() or item.suffix.lower() != ".json":
                    continue
                try:
                    partial = service_for(s).ingest_sources(
                        [jsonlib.loads(item.read_text(encoding="utf-8"))],
                        atomic=True,
                    )
                    sources.extend(partial["sources"])
                except Exception as exc:
                    errors.append({"file": str(item), "message": safe_error(exc)})
            if errors:
                emit(
                    "ingest",
                    "pending",
                    "run_" + __import__("uuid").uuid4().hex,
                    {"sources": sources, "file_errors": errors},
                    as_json=s["json"],
                )
                raise typer.Exit(1)
            result = {"run_id": "run_" + __import__("uuid").uuid4().hex, "sources": sources}
        else:
            result = service_for(s).ingest_sources(
                list(envelopes_from(file, directory, text)),
                atomic=atomic,
            )
        emit("ingest", "ok", result.pop("run_id"), result, as_json=s["json"])
    except typer.Exit:
        raise
    except Exception as exc:
        emit(
            "ingest",
            "error",
            "run_" + __import__("uuid").uuid4().hex,
            errors=[{"code": "schema", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@observe_app.command("add")
def observe_add(
    file: Optional[Path] = typer.Option(None, "--file"),
    stdin: bool = typer.Option(False, "--stdin"),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("observe add", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).add_observation(json_load(file, stdin))
        emit("observe add", "ok", result.pop("run_id"), result, as_json=s["json"])
    except Exception as exc:
        emit(
            "observe add",
            "error",
            "run_error",
            errors=[{"code": "schema", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@candidate_app.command("add")
def candidate_add(
    file: Optional[Path] = typer.Option(None, "--file"),
    stdin: bool = typer.Option(False, "--stdin"),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("candidate add", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).add_candidate(json_load(file, stdin))
        emit("candidate add", "ok", result.pop("run_id"), result, as_json=s["json"])
    except Exception as exc:
        emit(
            "candidate add",
            "error",
            "run_error",
            errors=[{"code": "schema", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@finding_app.command("add")
def finding_add(
    file: Optional[Path] = typer.Option(None, "--file"),
    stdin: bool = typer.Option(False, "--stdin"),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("finding add", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).add_finding(json_load(file, stdin))
        emit("finding add", "ok", result.pop("run_id"), result, as_json=s["json"])
    except Exception as exc:
        emit(
            "finding add",
            "error",
            "run_error",
            errors=[{"code": "schema", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@app.command("reconcile")
def reconcile_cmd(
    candidate_id: Optional[str] = typer.Option(None, "--candidate"),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("reconcile", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).reconcile(candidate_id)
        run = result.pop("run_id")
        result.pop("status", None)
        emit(
            "reconcile",
            "pending" if result["conflicted"] or result["invalid"] else "ok",
            run,
            result,
            as_json=s["json"],
        )
        if result["conflicted"] or result["invalid"]:
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as exc:
        emit(
            "reconcile",
            "error",
            "run_error",
            errors=[{"code": "configuration", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@app.command("publish")
def publish_cmd(
    ready: bool = typer.Option(False, "--ready"),
    publish_flag: bool = typer.Option(False, "--publish"),
    no_commit: bool = typer.Option(False, "--no-commit"),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("publish", library_root, state_root, project, actor, json)
    try:
        service = service_for(s)
        result = service.publish_ready(no_commit=no_commit) if publish_flag else service.publication_preview()
        run = result.pop("run_id", "run_" + __import__("uuid").uuid4().hex)
        emit("publish", "ok", run, result, as_json=s["json"])
    except Exception as exc:
        emit(
            "publish",
            "error",
            "run_error",
            errors=[{"code": "publication", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(getattr(exc, "exit_code", 3))


@app.command()
def status(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("status", library_root, state_root, project, actor, json)
    run = "run_" + __import__("uuid").uuid4().hex
    try:
        emit("status", "ok", run, service_for(s).status(), as_json=s["json"])
    except Exception as exc:
        emit("status", "error", run, errors=[{"code": "status", "message": safe_error(exc)}], as_json=s["json"])
        raise typer.Exit(2)


@app.command()
def validate(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("validate", library_root, state_root, project, actor, json)
    run = "run_" + __import__("uuid").uuid4().hex
    try:
        result = service_for(s).validate()
        emit("validate", "ok", run, result, as_json=s["json"])
    except Exception as exc:
        emit("validate", "error", run, errors=[{"code": "validation", "message": safe_error(exc)}], as_json=s["json"])
        raise typer.Exit(2)


@work_app.command("next")
def work_next(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("work next", library_root, state_root, project, actor, json)
    result = service_for(s).work_next()
    run = result.pop("run_id") if result else "run_" + __import__("uuid").uuid4().hex
    emit("work next", "pending" if result else "ok", run, {"item": result}, as_json=s["json"])
    if not result:
        raise typer.Exit(1)


@work_app.command("renew")
def work_renew(
    item_id: str = typer.Argument(...),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("work renew", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).work_renew(item_id)
        emit("work renew", "ok", result.pop("run_id"), result, as_json=s["json"])
    except ValueError as exc:
        emit("work renew", "error", "run_error", errors=[{"code": "lease", "message": str(exc)}], as_json=s["json"])
        raise typer.Exit(4)


@work_app.command("release")
def work_release(
    item_id: str = typer.Argument(...),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("work release", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).work_release(item_id)
        emit("work release", "ok", result.pop("run_id"), result, as_json=s["json"])
    except ValueError as exc:
        emit("work release", "error", "run_error", errors=[{"code": "lease", "message": str(exc)}], as_json=s["json"])
        raise typer.Exit(4)


@conflict_app.command("list")
def conflict_list(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("conflict list", library_root, state_root, project, actor, json)
    result = service_for(s).conflict_list()
    emit("conflict list", "ok", "run_" + __import__("uuid").uuid4().hex, result, as_json=s["json"])


@conflict_app.command("show")
def conflict_show(
    conflict_id: str = typer.Argument(...),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("conflict show", library_root, state_root, project, actor, json)
    try:
        packet = service_for(s).conflict_show(conflict_id)
    except KeyError:
        emit(
            "conflict show",
            "error",
            "run_" + __import__("uuid").uuid4().hex,
            errors=[{"code": "not_found", "message": conflict_id}],
            as_json=s["json"],
        )
        raise typer.Exit(2)
    emit(
        "conflict show",
        "ok",
        "run_" + __import__("uuid").uuid4().hex,
        packet.model_dump(mode="json", by_alias=True),
        as_json=s["json"],
    )


@conflict_app.command("resolve")
def conflict_resolve(
    conflict_id: str = typer.Argument(...),
    choice: str = typer.Option(..., "--choice"),
    rationale: Optional[str] = typer.Option(None, "--rationale"),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("conflict resolve", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).conflict_resolve(conflict_id, choice, rationale)
        emit("conflict resolve", "ok", result.pop("run_id"), result, as_json=s["json"])
    except (KeyError, ValueError) as exc:
        emit(
            "conflict resolve",
            "error",
            "run_error",
            errors=[{"code": "resolution", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@app.command("maintain")
def maintain(
    manifest: Path = typer.Option(..., "--manifest"),
    responses: Path = typer.Option(..., "--responses"),
    publish_flag: bool = typer.Option(False, "--publish"),
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("maintain", library_root, state_root, project, actor, json)
    try:
        manifest_data = jsonlib.loads(manifest.read_text(encoding="utf-8"))
        response_data = jsonlib.loads(responses.read_text(encoding="utf-8"))
        result = service_for(s).maintain(
            manifest_data if isinstance(manifest_data, list) else manifest_data.get("sources", []),
            response_data if isinstance(response_data, list) else response_data.get("responses", []),
            publish_changes=publish_flag,
        )
        emit("maintain", "ok", result.pop("run_id"), result, as_json=s["json"])
    except Exception as exc:
        emit(
            "maintain",
            "error",
            "run_error",
            errors=[{"code": "maintain", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


@migrate_app.command("legacy-pack")
def migrate_legacy_pack(
    library_root: Optional[Path] = typer.Option(None, "--library-root"),
    state_root: Optional[Path] = typer.Option(None),
    project: Optional[str] = typer.Option(None),
    actor: Optional[str] = typer.Option(None),
    publish_flag: bool = typer.Option(False, "--publish"),
    authorize_live_migration: bool = typer.Option(False, "--authorize-live-migration"),
    json: Optional[bool] = typer.Option(None, "--json"),
):
    s = require_settings("migrate legacy-pack", library_root, state_root, project, actor, json)
    try:
        result = service_for(s).migrate_legacy_pack(
            publish_changes=publish_flag,
            authorized=authorize_live_migration,
        )
        emit(
            "migrate legacy-pack",
            "ok",
            result.pop("run_id"),
            result,
            as_json=s["json"],
        )
    except Exception as exc:
        emit(
            "migrate legacy-pack",
            "error",
            "run_error",
            errors=[{"code": "migration", "message": safe_error(exc)}],
            as_json=s["json"],
        )
        raise typer.Exit(2)


def json_load(file: Path | None, use_stdin: bool) -> dict:
    if (file is None) == (not use_stdin):
        raise ValueError("exactly one of --file or --stdin is required")
    return jsonlib.loads(file.read_text(encoding="utf-8") if file else sys.stdin.read())


def json_load_payload(value: str) -> dict:
    return __import__("json").loads(value)


if __name__ == "__main__":
    app()
