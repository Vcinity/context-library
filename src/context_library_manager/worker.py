from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess

from context_library_maintainer.service import MaintainerApplicationService, MaintainerContext

from .agent import (
    AgentCancelled,
    invoke,
    provider_value_contains_secret,
    redact_evidence,
    redact_provider_value,
)
from .agent_service import complete_drain
from .config import Settings
from .configuration import effective_settings
from .db import Store
from .domain import AgentRequest, utc_now
from .security import safe_error_class

_DEFERRED = object()


class Worker:
    def __init__(self, store: Store, settings: Settings, owner: str = "worker-1"):
        self.store = store
        self.settings = settings
        self.owner = owner

    def _library_is_clean(self) -> bool:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.settings.library_root),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and not result.stdout.strip()

    def _wait_for_publication_review(self, work_id: str, reason: str) -> dict:
        self.store.create_review(
            self.settings.project,
            work_id,
            "Publication safety check failed; what should happen next?",
            ["retain-current", "retry"],
            [],
            self.owner,
        )
        self.store.transition(self.settings.project, work_id, "waiting-human", self.owner, reason)
        return {
            "work_id": work_id,
            "route": "deterministic",
            "status": "waiting-human",
            "reason": reason,
        }

    def run_once(self, deferred: set[str] | None = None) -> dict | None:
        deferred = deferred or set()
        while True:
            result = self._run_once(deferred)
            if result is not _DEFERRED:
                return result

    def _run_once(self, deferred: set[str]):
        deferred = deferred or set()
        self.settings = effective_settings(self.store, self.settings, self.settings.project)
        self.store.heartbeat("worker", self.owner, details={"project": self.settings.project})
        self.store.heartbeat("agent", self.owner, details={"project": self.settings.project})
        complete_drain(self.store, self.owner)
        service = self.store.service_state()
        row = self.store.claim(
            self.settings.project,
            self.owner,
            self.settings.lease_seconds,
            exclude_ids=deferred,
            deterministic_only=service["state"] in {"paused", "draining"},
            agent_concurrency_limit=self.settings.worker_concurrency,
        )
        if not row:
            return None
        work_id = row["id"]
        self.store.transition(self.settings.project, work_id, "running", self.owner)
        payload = json.loads(row["payload"])
        if payload.get("human_resolution") == "retain-current" and not payload.get("clm_payload"):
            self.store.transition(
                self.settings.project,
                work_id,
                "succeeded",
                self.owner,
                "human-retained-current",
            )
            return {"work_id": work_id, "route": "review", "status": "succeeded"}
        if row["item_type"] in {"source_batch", "publication_task"}:
            self.store.transition(self.settings.project, work_id, "succeeded", self.owner)
            return {"work_id": work_id, "route": "deterministic", "status": "succeeded"}
        if (
            row["item_type"] in {"observation_task", "candidate_task"}
            and payload.get("clm_payload", {}).get("schema_version") == 1
        ):
            maintainer = MaintainerApplicationService(
                MaintainerContext(
                    library_root=self.settings.library_root,
                    state_root=self.settings.state_root,
                    project=self.settings.project,
                    actor=self.owner,
                )
            )
            try:
                if row["item_type"] == "candidate_task" and payload.get("human_resolution") in {
                    "retain-current",
                    "adopt-candidate",
                }:
                    resolver = payload.get("human_resolver", self.owner)
                    resolution_service = MaintainerApplicationService(
                        MaintainerContext(
                            library_root=self.settings.library_root,
                            state_root=self.settings.state_root,
                            project=self.settings.project,
                            actor=resolver,
                        )
                    )
                    rationale = payload.get(
                        "human_resolution_rationale",
                        f"Manager review selected {payload['human_resolution']}.",
                    )
                    conflict_id = payload.get("maintainer_conflict_id")
                    if conflict_id:
                        candidate_id = payload["clm_payload"]["candidate_id"]
                        choice = (
                            f"accept:{candidate_id}"
                            if payload["human_resolution"] == "adopt-candidate"
                            else "retain-current"
                        )
                        conflict = resolution_service.conflict_show(conflict_id)
                        if conflict.status == "open":
                            resolved = resolution_service.conflict_resolve(
                                conflict_id,
                                choice,
                                rationale,
                            )
                        else:
                            if conflict.resolution is None or conflict.resolution.choice != choice:
                                raise ValueError("Maintainer conflict resolution does not match Manager review")
                            resolved = {
                                "conflict_id": conflict_id,
                                **conflict.resolution.model_dump(mode="json"),
                            }
                        if choice == "retain-current":
                            self.store.transition(
                                self.settings.project,
                                work_id,
                                "succeeded",
                                self.owner,
                                "human-retained-current",
                            )
                            return {
                                "work_id": work_id,
                                "route": "review",
                                "status": "succeeded",
                                "maintainer": resolved,
                            }
                        resolution_candidate_id = resolved.get("resolution_candidate_id")
                        if not resolution_candidate_id:
                            raise ValueError("conflict adoption did not create a resolution candidate")
                        reconciled = resolution_service.reconcile(resolution_candidate_id)
                        if reconciled["status"] != "ok":
                            raise ValueError("human conflict resolution did not reconcile cleanly")
                        if payload.get("authorized_publication"):
                            if not self._library_is_clean():
                                return self._wait_for_publication_review(work_id, "dirty-library-worktree")
                            published = self._publish_ready(work_id, resolution_service)
                            if resolution_candidate_id not in published.get("published", []):
                                raise ValueError("authorized resolution candidate was not published")
                            self._record_publication(work_id, published)
                        self.store.transition(self.settings.project, work_id, "succeeded", self.owner)
                        return {
                            "work_id": work_id,
                            "route": "review",
                            "status": "succeeded",
                            "maintainer": resolved,
                        }
                    if payload["human_resolution"] == "retain-current":
                        rejected = resolution_service.reject_candidate(
                            payload["clm_payload"]["candidate_id"],
                            rationale,
                        )
                        self.store.transition(
                            self.settings.project,
                            work_id,
                            "succeeded",
                            self.owner,
                            "human-retained-current",
                        )
                        return {
                            "work_id": work_id,
                            "route": "review",
                            "status": "succeeded",
                            "maintainer": rejected,
                        }
                if (
                    row["item_type"] == "candidate_task"
                    and payload.get("human_resolution") == "adopt-candidate"
                    and payload.get("authorized_publication")
                ):
                    if not self._library_is_clean():
                        return self._wait_for_publication_review(work_id, "dirty-library-worktree")
                    published = self._publish_ready(work_id, maintainer)
                    self._record_publication(work_id, published)
                    self.store.transition(self.settings.project, work_id, "succeeded", self.owner)
                    return {
                        "work_id": work_id,
                        "route": "review",
                        "status": "succeeded",
                        "maintainer": published,
                    }
                result = (
                    maintainer.add_observation(payload["clm_payload"])
                    if row["item_type"] == "observation_task"
                    else maintainer.add_candidate(payload["clm_payload"])
                )
                if row["item_type"] == "candidate_task":
                    reconciled = maintainer.reconcile(payload["clm_payload"]["candidate_id"])
                    if reconciled["status"] != "ok":
                        if reconciled.get("conflicted"):
                            candidate_id = payload["clm_payload"]["candidate_id"]
                            packet = None
                            for item in maintainer.conflict_list()["conflicts"]:
                                if item["status"] != "open":
                                    continue
                                candidate_packet = maintainer.conflict_show(item["id"])
                                if candidate_id in candidate_packet.candidate_ids:
                                    packet = candidate_packet
                                    break
                            if packet is None:
                                raise ValueError("reconciliation conflict did not expose a conflict packet")
                            payload["maintainer_conflict_id"] = packet.conflict_id
                            self.store.db.execute(
                                "UPDATE work_items SET payload=?,updated_at=? WHERE id=?",
                                (json.dumps(payload), utc_now(), work_id),
                            )
                        self.store.create_review(
                            self.settings.project,
                            work_id,
                            "Candidate reconciliation requires human review.",
                            ["retain-current", "adopt-candidate"],
                            payload.get("evidence", []),
                            self.owner,
                        )
                        self.store.transition(
                            self.settings.project,
                            work_id,
                            "waiting-human",
                            self.owner,
                            json.dumps(reconciled, sort_keys=True),
                        )
                        return {
                            "work_id": work_id,
                            "route": "deterministic",
                            "status": "waiting-human",
                            "reason": "reconciliation",
                            "maintainer": reconciled,
                        }
                    if payload.get("publication_intent") and not payload.get("authorized_publication"):
                        self.store.create_review(
                            self.settings.project,
                            work_id,
                            "The candidate is ready. Should an administrator publish it?",
                            ["retain-current", "adopt-candidate"],
                            payload.get("evidence", []),
                            self.owner,
                        )
                        self.store.transition(
                            self.settings.project,
                            work_id,
                            "waiting-human",
                            self.owner,
                            "publication-approval-required",
                        )
                        return {
                            "work_id": work_id,
                            "route": "review",
                            "status": "waiting-human",
                            "reason": "publication-approval-required",
                        }
                    if payload.get("authorized_publication"):
                        if not self._library_is_clean():
                            return self._wait_for_publication_review(work_id, "dirty-library-worktree")
                        published = self._publish_ready(work_id, maintainer)
                        self._record_publication(work_id, published)
                self.store.transition(self.settings.project, work_id, "succeeded", self.owner)
                return {
                    "work_id": work_id,
                    "route": "deterministic",
                    "status": "succeeded",
                    "maintainer": result,
                }
            except Exception as exc:
                self.store.transition(
                    self.settings.project,
                    work_id,
                    "failed",
                    self.owner,
                    type(exc).__name__,
                )
                return {
                    "work_id": work_id,
                    "route": "deterministic",
                    "status": "failed",
                    "reason": type(exc).__name__,
                }
        result = self._run_agent(row, payload)
        if result.get("status") == "queued" and result.get("reason", "").startswith("agent-service-"):
            deferred.add(work_id)
            return _DEFERRED
        return result

    def _run_agent(self, row, payload: dict) -> dict:
        work_id = row["id"]
        profile = payload.get("model_profile", "cheap")
        max_output = (
            self.settings.cheap_profile_max_tokens if profile == "cheap" else self.settings.standard_profile_max_tokens
        )
        evidence = redact_evidence(payload.get("evidence", []))
        task_context = {
            key: payload[key]
            for key in (
                "proposal_id",
                "operation",
                "decision_id",
                "proposed_fields",
                "rationale",
                "authority",
                "publication_intent",
                "base_library_digest",
            )
            if key in payload
        }
        task_context = redact_provider_value(task_context)
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "project": self.settings.project,
                    "task_type": row["item_type"],
                    "evidence": evidence,
                    "task_context": task_context,
                    "policy": payload.get("policy_revision", "1"),
                    "prompt": payload.get("prompt_revision", "1"),
                    "profile": profile,
                    "schema": payload.get("required_output_schema", "candidate-v1"),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if self.settings.cache_enabled:
            cached = self.store.cache_get(cache_key)
            if cached:
                cached_payload = json.loads(cached["payload"])
                if provider_value_contains_secret(cached_payload):
                    cached = None
            if cached:
                self.store.record_agent_run(
                    f"agentrun_cache_{work_id}",
                    work_id,
                    profile,
                    "ok",
                    0,
                    0,
                    True,
                    actor=self.owner,
                    prompt_revision=payload.get("prompt_revision", "1"),
                    cache_key=cache_key,
                )
                if row["item_type"] == "candidate_task":
                    self._persist_candidate_result(work_id, payload, cached_payload)
                    self.store.transition(self.settings.project, work_id, "queued", self.owner)
                    return {
                        "work_id": work_id,
                        "status": "queued",
                        "reason": "candidate-normalized",
                        "cache_hit": True,
                    }
                self.store.transition(self.settings.project, work_id, "succeeded", self.owner)
                return {"work_id": work_id, "status": "succeeded", "cache_hit": True}
        service = self.store.service_state()
        if service["state"] in {"paused", "draining"}:
            self.store.transition(
                self.settings.project,
                work_id,
                "queued",
                self.owner,
                f"agent-service-{service['state']}",
            )
            complete_drain(self.store, self.owner)
            return {
                "work_id": work_id,
                "status": "queued",
                "reason": f"agent-service-{service['state']}",
            }
        if not self.settings.autonomy_enabled:
            self.store.create_review(
                self.settings.project,
                work_id,
                "Autonomous agent dispatch is disabled; what should happen next?",
                ["retain-current", "reassess"],
                evidence,
                self.owner,
            )
            self.store.transition(
                self.settings.project,
                work_id,
                "waiting-human",
                self.owner,
                "autonomy-disabled",
            )
            return {
                "work_id": work_id,
                "status": "waiting-human",
                "reason": "autonomy-disabled",
            }
        if payload.get("category") in self.settings.excluded_categories:
            self.store.create_review(
                self.settings.project,
                work_id,
                "Project policy excludes autonomous processing for this category.",
                ["retain-current", "reassess"],
                evidence,
                self.owner,
            )
            self.store.transition(
                self.settings.project,
                work_id,
                "waiting-human",
                self.owner,
                "excluded-category",
            )
            return {
                "work_id": work_id,
                "status": "waiting-human",
                "reason": "excluded-category",
            }
        reserved = min(self.settings.item_token_budget, max_output)
        if not self.store.reserve_budget(
            self.settings.project,
            self.owner,
            reserved,
            self.settings.project_daily_token_budget,
            self.settings.user_daily_token_budget,
        ):
            self.store.create_review(
                self.settings.project,
                work_id,
                "Budget exhausted; what should happen to this item?",
                ["defer", "authorize-more"],
                [],
                self.owner,
            )
            self.store.transition(
                self.settings.project,
                work_id,
                "waiting-human",
                self.owner,
                "budget-exhausted",
            )
            return {
                "work_id": work_id,
                "status": "waiting-human",
                "reason": "budget-exhausted",
            }
        command = shlex.split(os.getenv("CLM_AGENT_COMMAND", ""))
        attempt = row["attempts"]
        run_id = f"agentrun_{hashlib.sha256(f'{work_id}:{attempt}'.encode()).hexdigest()[:24]}"
        request = AgentRequest(
            run_id=run_id,
            project=self.settings.project,
            task_type=row["item_type"],
            actor=self.owner,
            model_profile=profile,
            budget={
                "max_input_tokens": min(self.settings.item_token_budget, 2_000),
                "max_output_tokens": max_output,
            },
            prompt_revision=payload.get("prompt_revision", "1"),
            evidence=evidence,
            task_context=task_context,
            required_output_schema=payload.get("required_output_schema", "candidate-v1"),
        )
        if not command:
            self.store.settle_budget(self.settings.project, self.owner, reserved, 0)
            self.store.create_review(
                self.settings.project,
                work_id,
                "No agent adapter is configured; how should this semantic item be resolved?",
                ["defer", "retry"],
                evidence,
                self.owner,
            )
            self.store.transition(
                self.settings.project,
                work_id,
                "waiting-human",
                self.owner,
                "agent-not-configured",
            )
            return {
                "work_id": work_id,
                "status": "waiting-human",
                "reason": "agent-not-configured",
            }
        self.store.start_agent_run(
            run_id,
            work_id,
            profile,
            self.owner,
            request.prompt_revision,
            cache_key,
            reserved_tokens=reserved,
            invocation_reason=payload.get("routing_reason", f"semantic-{row['item_type']}-required"),
            deterministic_checks=tuple(
                payload.get(
                    "deterministic_checks",
                    ["schema", "identity", "topology", "exact-cache"],
                )
            ),
            cache_check="miss",
        )
        actual_input = 0
        actual_output = 0
        settled = False
        try:
            result = invoke(
                command,
                request.model_dump(),
                cancellation_requested=lambda: self.store.cancellation_requested(run_id),
            )
            actual_input = result.input_tokens
            actual_output = result.output_tokens
            actual = actual_input + actual_output
            if self.store.cancellation_requested(run_id):
                self.store.settle_budget(self.settings.project, self.owner, reserved, actual)
                settled = True
                self.store.acknowledge_cancellation(
                    self.settings.project,
                    work_id,
                    run_id,
                    self.owner,
                    result.input_tokens,
                    result.output_tokens,
                )
                complete_drain(self.store, self.owner)
                return {"work_id": work_id, "status": "canceled"}
            if result.status != "ok":
                raise ValueError("agent returned an error status")
            self.store.settle_budget(self.settings.project, self.owner, reserved, actual)
            settled = True
            self.store.finish_agent_run(run_id, result.status, result.input_tokens, result.output_tokens)
            if row["item_type"] == "candidate_task":
                self._persist_candidate_result(work_id, payload, result.payload)
            if result.confidence is not None and result.confidence < self.settings.semantic_threshold:
                self.store.create_review(
                    self.settings.project,
                    work_id,
                    "Agent confidence is below the project threshold.",
                    ["retain-current", "adopt-candidate"],
                    evidence,
                    self.owner,
                )
                self.store.transition(
                    self.settings.project,
                    work_id,
                    "waiting-human",
                    self.owner,
                    "confidence-below-threshold",
                )
                return {
                    "work_id": work_id,
                    "status": "waiting-human",
                    "reason": "confidence-below-threshold",
                }
            if self.settings.cache_enabled:
                self.store.cache_put(
                    cache_key,
                    self.settings.project,
                    result.payload,
                    result.input_tokens,
                    result.output_tokens,
                )
            if row["item_type"] == "candidate_task":
                self.store.transition(self.settings.project, work_id, "queued", self.owner)
                return {
                    "work_id": work_id,
                    "status": "queued",
                    "reason": "candidate-normalized",
                    "cache_hit": False,
                }
            self.store.transition(self.settings.project, work_id, "succeeded", self.owner)
            return {"work_id": work_id, "status": "succeeded", "cache_hit": False}
        except Exception as exc:
            error_class = type(exc).__name__
            if not settled:
                self.store.settle_budget(
                    self.settings.project,
                    self.owner,
                    reserved,
                    actual_input + actual_output,
                )
            if isinstance(exc, AgentCancelled) or self.store.cancellation_requested(run_id):
                self.store.acknowledge_cancellation(self.settings.project, work_id, run_id, self.owner)
                complete_drain(self.store, self.owner)
                return {"work_id": work_id, "status": "canceled"}
            self.store.finish_agent_run(run_id, "error", actual_input, actual_output)
            attempts = row["attempts"] + 1
            if attempts >= self.settings.max_attempts:
                self.store.create_review(
                    self.settings.project,
                    work_id,
                    "Agent execution failed; what should happen next?",
                    ["defer", "retry"],
                    evidence,
                    self.owner,
                )
                self.store.transition(
                    self.settings.project,
                    work_id,
                    "waiting-human",
                    self.owner,
                    error_class,
                )
                return {
                    "work_id": work_id,
                    "status": "waiting-human",
                    "reason": error_class,
                }
            self.store.transition(self.settings.project, work_id, "retryable", self.owner, error_class)
            return {"work_id": work_id, "status": "retryable", "reason": error_class}

    def _persist_candidate_result(self, work_id: str, payload: dict, candidate: dict) -> None:
        if (
            not isinstance(candidate, dict)
            or candidate.get("schema") != "context-library/candidate"
            or candidate.get("schema_version") != 1
            or not isinstance(candidate.get("candidate_id"), str)
        ):
            raise ValueError("agent candidate result is not a candidate-v1 payload")
        payload["clm_payload"] = candidate
        self.store.db.execute(
            "UPDATE work_items SET payload=?, updated_at=? WHERE id=?",
            (json.dumps(payload), utc_now(), work_id),
        )
        self.store.db.commit()

    def _record_publication(self, work_id: str, published: dict) -> None:
        publication_id = f"publication_{self.store.digest([work_id, 'succeeded'])[:24]}"
        self.store.db.execute(
            "INSERT INTO publication_history"
            "(id,project,work_id,status,digest,created_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
            (
                publication_id,
                self.settings.project,
                work_id,
                "succeeded",
                self.store.digest(published),
                utc_now(),
            ),
        )
        from .telemetry import append_event

        append_event(
            self.store,
            self.settings.project,
            "work",
            "publication-succeeded",
            item_id=work_id,
            payload={"publication_id": publication_id},
            commit=False,
        )
        self.store.event(
            work_id,
            self.owner,
            "publication-succeeded",
            {"publication_id": publication_id},
            self.settings.project,
        )
        self.store.db.commit()

    def _record_publication_failure(self, work_id: str, exc: BaseException) -> None:
        error_class = safe_error_class(exc)
        created_at = utc_now()
        publication_id = f"publication_{self.store.digest([work_id, 'failed', created_at])[:24]}"
        self.store.db.execute(
            "INSERT INTO publication_history(id,project,work_id,status,error,created_at) VALUES(?,?,?,?,?,?)",
            (
                publication_id,
                self.settings.project,
                work_id,
                "failed",
                error_class,
                created_at,
            ),
        )
        self.store.event(
            work_id,
            self.owner,
            "publication-failed",
            {
                "publication_id": publication_id,
                "error_class": error_class,
            },
            self.settings.project,
        )
        from .telemetry import append_event

        append_event(
            self.store,
            self.settings.project,
            "work",
            "publication-failed",
            item_id=work_id,
            payload={
                "publication_id": publication_id,
                "error_class": error_class,
            },
            commit=False,
        )
        self.store.db.commit()

    def _publish_ready(
        self,
        work_id: str,
        maintainer: MaintainerApplicationService,
    ) -> dict:
        try:
            return maintainer.publish_ready()
        except Exception as exc:
            self._record_publication_failure(work_id, exc)
            raise
