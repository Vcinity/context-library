from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import Any, Callable

from .config import ConfigError, project_files, resolve_config, safe_path, scaffold
from .ingest import ingest
from .models import (
    Candidate,
    ConflictPacket,
    ConflictResolution,
    Finding,
    Observation,
    Person,
    SourceEnvelope,
    digest,
    now_utc,
    timestamp,
)
from .publish import _indexes, publish
from .query import query_library
from .reconcile import reconcile
from .state import State


@dataclass(frozen=True)
class MaintainerContext:
    library_root: Path
    state_root: Path
    project: str
    actor: str
    timeout_seconds: float = 30.0
    cancelled: Callable[[], bool] | None = None


class MaintainerCancelledError(RuntimeError):
    pass


class MaintainerTimeoutError(TimeoutError):
    pass


class MaintainerApplicationService:
    """Typed in-process boundary used by Manager and the administrative CLI."""

    def __init__(self, context: MaintainerContext):
        self.context = context

    def _settings(self) -> dict[str, Any]:
        return resolve_config(
            self.context.library_root,
            project=self.context.project,
            state_root=self.context.state_root,
            actor=self.context.actor,
            json_output=False,
        )

    def _state(self) -> State:
        return State(self.context.state_root)

    def _conflict_packet(self, payload_json: str) -> ConflictPacket:
        payload = json.loads(payload_json)
        payload.setdefault("schema", "context-library/conflict-packet")
        payload.setdefault("schema_version", 1)
        payload.setdefault("project", self.context.project)
        return ConflictPacket.model_validate(payload)

    def _boundary(self, started: float) -> None:
        if self.context.cancelled and self.context.cancelled():
            raise MaintainerCancelledError("Maintainer operation was cancelled")
        if monotonic() - started > self.context.timeout_seconds:
            raise MaintainerTimeoutError("Maintainer operation exceeded its timeout")

    def _run(self, state: State, command: str, input_identity: str) -> tuple[str, float]:
        started = monotonic()
        try:
            self._boundary(started)
        except BaseException:
            state.db.close()
            raise
        run_id = state.run_start(
            self.context.actor,
            command,
            {
                "project": self.context.project,
                "input_identity": input_identity,
                "policy_revision": "project-config-v1",
            },
        )
        return run_id, started

    def query(
        self,
        *,
        project: str | None = None,
        query: str = "",
        decision_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
        page: int = 1,
        page_size: int = 25,
        digest_only: bool = False,
    ) -> dict[str, Any]:
        return query_library(
            self.context.library_root,
            project or self.context.project,
            query=query,
            decision_id=decision_id,
            status=status,
            category=category,
            page=page,
            page_size=page_size,
            digest_only=digest_only,
        )

    def initialize(self) -> dict[str, Any]:
        state = self._state()
        run_id, started = self._run(state, "init", self.context.project)
        try:
            created = scaffold(self._settings())
            self._boundary(started)
            state.run_end(run_id, "ok", touched=created)
            return {"run_id": run_id, "scaffolded": created}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def status(self) -> dict[str, Any]:
        state = self._state()
        try:
            return state.status(self.context.project)
        finally:
            state.db.close()

    def validate(self) -> dict[str, Any]:
        from .validate import validate_pack

        root, config, _, _ = project_files(self._settings())
        return validate_pack(root, config.register)

    def ingest_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = SourceEnvelope.model_validate(payload)
        state = self._state()
        run_id, started = self._run(state, "ingest", digest(source.model_dump(mode="json")))
        try:
            with state.transaction():
                data = ingest(state, [source.model_dump(mode="json")], self.context.project)
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {"run_id": run_id, "sources": data}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def ingest_sources(self, payloads: list[dict[str, Any]], *, atomic: bool = True) -> dict[str, Any]:
        sources = [SourceEnvelope.model_validate(payload) for payload in payloads]
        state = self._state()
        run_id, started = self._run(state, "ingest", digest([item.model_dump(mode="json") for item in sources]))
        try:
            retain = True
            try:
                _, config, _, _ = project_files(self._settings())
                retain = config.policies.retain_source_content
            except ConfigError:
                pass
            if atomic:
                with state.transaction():
                    data = ingest(
                        state,
                        [source.model_dump(mode="json") for source in sources],
                        self.context.project,
                        retain=retain,
                    )
            else:
                data = ingest(
                    state,
                    [source.model_dump(mode="json") for source in sources],
                    self.context.project,
                    retain=retain,
                )
            self._boundary(started)
            state.run_end(run_id, "ok", touched=[item["source_id"] for item in data])
            return {"run_id": run_id, "sources": data}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def add_observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        observation = Observation.model_validate(payload)
        state = self._state()
        run_id, started = self._run(state, "observe add", digest(observation.model_dump(mode="json")))
        try:
            observation_id = "obs_" + digest(observation.model_dump())[:24]
            with state.transaction():
                state.add_observation(observation, observation_id, self.context.project)
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {"run_id": run_id, "observation_id": observation_id}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def add_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        candidate = Candidate.model_validate(payload)
        state = self._state()
        run_id, started = self._run(state, "candidate add", candidate.candidate_id)
        try:
            if candidate.project != self.context.project:
                raise ValueError("candidate project does not match Maintainer service context")
            with state.transaction():
                state.add_candidate(candidate)
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {"run_id": run_id, "candidate_id": candidate.candidate_id}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def add_finding(self, payload: dict[str, Any]) -> dict[str, Any]:
        finding = Finding.model_validate(payload)
        state = self._state()
        run_id, started = self._run(state, "finding add", finding.candidate_id)
        try:
            with state.transaction():
                state.add_finding(finding, self.context.project)
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {"run_id": run_id, "candidate_id": finding.candidate_id}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def reconcile(self, candidate_id: str | None = None) -> dict[str, Any]:
        state = self._state()
        run_id, started = self._run(state, "reconcile", candidate_id or "all-proposed")
        try:
            with state.transaction():
                data = reconcile(state, self._settings(), candidate_id)
            self._boundary(started)
            status = "pending" if data["conflicted"] or data["invalid"] else "ok"
            state.run_end(run_id, status)
            return {"run_id": run_id, "status": status, **data}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def publish_ready(self, *, no_commit: bool = False) -> dict[str, Any]:
        settings = self._settings()
        _, config, _, _ = project_files(settings)
        if not config.policies.automatic_publication:
            raise ValueError("automatic publication is disabled by project policy")
        state = self._state()
        run_id, started = self._run(state, "publish", "ready-candidates")
        try:
            settings["run_id"] = run_id
            data = publish(state, settings, ready_only=True, no_commit=no_commit)
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {"run_id": run_id, **data}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def publication_preview(self) -> dict[str, Any]:
        state = self._state()
        try:
            return {
                "dry_run": True,
                "ready": [row["id"] for row in state.candidates(self.context.project, "ready")],
            }
        finally:
            state.db.close()

    def reject_candidate(self, candidate_id: str, rationale: str) -> dict[str, Any]:
        state = self._state()
        run_id, started = self._run(state, "candidate reject", candidate_id)
        try:
            with state.project_lock(self.context.project, self.context.actor), state.transaction():
                row = state.db.execute(
                    "SELECT state FROM candidates WHERE id=? AND project=?",
                    (candidate_id, self.context.project),
                ).fetchone()
                if row is None:
                    raise KeyError(candidate_id)
                if row["state"] == "published":
                    raise ValueError("published candidates cannot be rejected")
                if row["state"] != "rejected":
                    state.transition(candidate_id, "rejected", rationale)
            self._boundary(started)
            state.run_end(run_id, "ok", touched=[candidate_id])
            return {"run_id": run_id, "candidate_id": candidate_id, "state": "rejected"}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def work_next(self) -> dict[str, Any] | None:
        state = self._state()
        run_id, started = self._run(state, "work next", "next-available")
        try:
            with state.transaction():
                now = now_utc()
                source_rows = list(
                    state.db.execute(
                        "SELECT id FROM sources WHERE project=? ORDER BY created_at, id",
                        (self.context.project,),
                    )
                )
                candidate_rows = state.candidates(self.context.project, "proposed") + state.candidates(
                    self.context.project, "ready"
                )
                choices = [("source", row["id"]) for row in source_rows] + [
                    ("candidate", row["id"]) for row in candidate_rows
                ]
                selected: tuple[str, str] | None = None
                for item_type, item_id in choices:
                    lease = state.db.execute(
                        "SELECT expires_at FROM leases WHERE item_type=? AND item_id=?",
                        (item_type, item_id),
                    ).fetchone()
                    if lease is not None and lease["expires_at"] > timestamp(now):
                        continue
                    state.db.execute(
                        "DELETE FROM leases WHERE item_type=? AND item_id=?",
                        (item_type, item_id),
                    )
                    state.db.execute(
                        "INSERT INTO leases(item_type,item_id,actor,leased_at,expires_at) VALUES(?,?,?,?,?)",
                        (
                            item_type,
                            item_id,
                            self.context.actor,
                            timestamp(now),
                            timestamp(now + timedelta(minutes=30)),
                        ),
                    )
                    selected = (item_type, item_id)
                    break
            self._boundary(started)
            state.run_end(run_id, "ok" if selected else "pending")
            return (
                {
                    "run_id": run_id,
                    "item_type": selected[0],
                    "item_id": selected[1],
                    "actor": self.context.actor,
                }
                if selected
                else None
            )
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def work_renew(self, item_id: str) -> dict[str, Any]:
        state = self._state()
        run_id, started = self._run(state, "work renew", item_id)
        try:
            expires = now_utc() + timedelta(minutes=30)
            with state.transaction():
                changed = state.db.execute(
                    "UPDATE leases SET expires_at=? WHERE item_id=? AND actor=?",
                    (timestamp(expires), item_id, self.context.actor),
                ).rowcount
                if not changed:
                    raise ValueError("active lease not found")
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {"run_id": run_id, "item_id": item_id, "expires_at": timestamp(expires)}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def work_release(self, item_id: str) -> dict[str, Any]:
        state = self._state()
        run_id, started = self._run(state, "work release", item_id)
        try:
            with state.transaction():
                changed = state.db.execute(
                    "DELETE FROM leases WHERE item_id=? AND actor=?",
                    (item_id, self.context.actor),
                ).rowcount
                if not changed:
                    raise ValueError("active lease not found")
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {"run_id": run_id, "item_id": item_id}
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def conflict_list(self) -> dict[str, Any]:
        state = self._state()
        try:
            rows = [
                dict(row)
                for row in state.db.execute(
                    "SELECT id,status,created_at,updated_at FROM conflicts WHERE project=? ORDER BY created_at,id",
                    (self.context.project,),
                )
            ]
            return {"conflicts": rows}
        finally:
            state.db.close()

    def conflict_show(self, conflict_id: str) -> ConflictPacket:
        state = self._state()
        try:
            row = state.db.execute(
                "SELECT payload_json FROM conflicts WHERE id=? AND project=?",
                (conflict_id, self.context.project),
            ).fetchone()
            if row is None:
                raise KeyError(conflict_id)
            return self._conflict_packet(row["payload_json"])
        finally:
            state.db.close()

    def conflict_resolve(
        self,
        conflict_id: str,
        choice: str,
        rationale: str | None = None,
    ) -> dict[str, Any]:
        state = self._state()
        run_id, started = self._run(state, "conflict resolve", conflict_id)
        try:
            with state.project_lock(self.context.project, self.context.actor), state.transaction():
                row = state.db.execute(
                    "SELECT payload_json FROM conflicts WHERE id=? AND project=? AND status='open'",
                    (conflict_id, self.context.project),
                ).fetchone()
                if row is None:
                    raise KeyError(conflict_id)
                packet = self._conflict_packet(row["payload_json"])
                if choice not in {item.value for item in packet.choices}:
                    raise ValueError("choice is not listed in conflict packet")
                resolution_source_id: str | None = None
                resolution_candidate_id: str | None = None
                if choice.startswith("accept:"):
                    selected = choice.split(":", 1)[1]
                    selected_row = state.db.execute(
                        "SELECT payload_json FROM candidates WHERE id=? AND project=?",
                        (selected, self.context.project),
                    ).fetchone()
                    if selected_row is None:
                        raise KeyError(selected)
                    original = Candidate.model_validate_json(selected_row["payload_json"])
                    resolution_text = rationale or f"Human resolution selected {choice}."
                    decided_at = now_utc()
                    envelope = SourceEnvelope(
                        external_id=f"human-resolution:{conflict_id}",
                        source_type="other",
                        uri=f"clm://conflict/{conflict_id}",
                        title=f"Resolution for {conflict_id}",
                        author=Person(identity=self.context.actor, display_name=self.context.actor),
                        created_at=decided_at,
                        retrieved_at=decided_at,
                        content_format="text",
                        content=resolution_text,
                    )
                    resolution_source_id, _ = state.add_source(envelope, self.context.project)
                    observation = Observation(
                        source_id=resolution_source_id,
                        kind="directive",
                        excerpt=resolution_text,
                        location="human resolution",
                        speaker=Person(identity=self.context.actor, display_name=self.context.actor),
                        occurred_at=decided_at,
                        agent_interpretation="Human-selected conflict resolution.",
                    )
                    observation_id = "obs_" + digest(observation.model_dump())[:24]
                    state.add_observation(observation, observation_id, self.context.project)
                    resolution_candidate_id = f"{original.candidate_id}-resolution"
                    resolved = original.model_copy(
                        update={
                            "candidate_id": resolution_candidate_id,
                            "decisionmaker": Person(identity=self.context.actor, display_name=self.context.actor),
                            "decision_at": decided_at,
                            "source_observation_ids": [observation_id],
                            "supersedes": sorted(set((*original.supersedes, *packet.canonical_ids))),
                            "review": original.review.model_copy(update={"status": "unreviewed"}),
                        }
                    )
                    state.add_candidate(resolved)
                    for candidate_id in packet.candidate_ids:
                        state.transition(candidate_id, "rejected", "replaced by human resolution")
                else:
                    for candidate_id in packet.candidate_ids:
                        state.transition(candidate_id, "rejected", "human retained current direction")
                resolution = ConflictResolution(
                    choice=choice,
                    rationale=rationale,
                    resolver=self.context.actor,
                    resolved_at=now_utc(),
                    resolution_source_id=resolution_source_id,
                    resolution_candidate_id=resolution_candidate_id,
                )
                resolved_packet = packet.model_copy(update={"status": "resolved", "resolution": resolution})
                changed = state.db.execute(
                    "UPDATE conflicts SET status='resolved',payload_json=?,updated_at=? "
                    "WHERE id=? AND project=? AND status='open'",
                    (
                        resolved_packet.model_dump_json(by_alias=True),
                        timestamp(now_utc()),
                        conflict_id,
                        self.context.project,
                    ),
                ).rowcount
                if changed != 1:
                    raise RuntimeError("conflict was resolved concurrently")
            self._boundary(started)
            touched = [
                conflict_id,
                *(item for item in (resolution_source_id, resolution_candidate_id) if item),
            ]
            state.run_end(run_id, "ok", touched=touched)
            return {
                "run_id": run_id,
                "conflict_id": conflict_id,
                **resolution.model_dump(mode="json", by_alias=True),
            }
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def migrate_legacy_pack(self, *, publish_changes: bool, authorized: bool) -> dict[str, Any]:
        settings = self._settings()
        state = self._state()
        run_id, started = self._run(state, "migrate legacy-pack", self.context.project)
        source = settings["library_root"] / "decision-artifacts" / "decision-register.md"
        destination = settings["library_root"] / "projects" / self.context.project / "decision-register.md"
        try:
            safe_path(settings["library_root"], source)
            safe_path(settings["library_root"], destination, allow_missing=True)
            if not source.is_file():
                raise ConfigError(f"legacy register is unavailable: {source}")
            source_bytes = source.read_bytes()
            text = source_bytes.decode("utf-8")
            from context_library_core.canonical import decision_ids

            identifiers = decision_ids(text)
            if publish_changes:
                if not authorized:
                    raise ConfigError("published migration requires separate authorization")
                if destination.exists():
                    raise ConfigError(f"destination register already exists: {destination}")
                project_root = destination.parent
                if not all(
                    (project_root / name).is_file() for name in ("maintainer.yaml", "topology.yaml", "authority.yaml")
                ):
                    raise ConfigError("initialize the target project before publishing a legacy migration")
                with state.project_lock(self.context.project, self.context.actor):
                    temporary = destination.with_name(f".{destination.name}.migration-tmp")
                    temporary.write_bytes(source_bytes)
                    os.replace(temporary, destination)
                    for name, content in _indexes(text).items():
                        target = project_root / name
                        temporary = target.with_name(f".{target.name}.migration-tmp")
                        temporary.write_text(content, encoding="utf-8")
                        os.replace(temporary, target)
            self._boundary(started)
            touched = [str(destination)] if publish_changes else []
            state.run_end(run_id, "ok", touched=touched)
            return {
                "run_id": run_id,
                "dry_run": not publish_changes,
                "project": self.context.project,
                "source": str(source),
                "destination": str(destination),
                "decision_ids": identifiers,
            }
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()

    def maintain(
        self,
        source_payloads: list[dict[str, Any]],
        responses: list[dict[str, Any]],
        *,
        publish_changes: bool = False,
    ) -> dict[str, Any]:
        state = self._state()
        run_id, started = self._run(state, "maintain", digest([source_payloads, responses]))
        settings = self._settings()
        try:
            _, config, _, _ = project_files(settings)
            with state.transaction():
                ingested = ingest(
                    state,
                    source_payloads,
                    self.context.project,
                    retain=config.policies.retain_source_content,
                )
                for response in responses:
                    kind = response.get("kind") or response.get("type")
                    payload = response.get("payload", response)
                    if kind == "observation":
                        observation = Observation.model_validate(payload)
                        observation_id = "obs_" + digest(observation.model_dump())[:24]
                        state.add_observation(observation, observation_id, self.context.project)
                    elif kind == "candidate":
                        candidate = Candidate.model_validate(payload)
                        if candidate.project != self.context.project:
                            raise ValueError("candidate project does not match Maintainer service context")
                        state.add_candidate(candidate)
                    elif kind == "finding":
                        state.add_finding(Finding.model_validate(payload), self.context.project)
                    else:
                        raise ValueError(f"unknown maintenance response kind: {kind}")
                reconciled = reconcile(state, settings)
            published: dict[str, Any] = {"dry_run": True}
            if publish_changes:
                if not config.policies.automatic_publication:
                    raise ValueError("automatic publication is disabled by project policy")
                settings["run_id"] = run_id
                published = publish(state, settings, ready_only=True)
            self._boundary(started)
            state.run_end(run_id, "ok")
            return {
                "run_id": run_id,
                "ingested": ingested,
                "responses": len(responses),
                "reconcile": reconciled,
                "publish": published,
            }
        except Exception as exc:
            state.run_end(run_id, "error", type(exc).__name__)
            raise
        finally:
            state.db.close()
