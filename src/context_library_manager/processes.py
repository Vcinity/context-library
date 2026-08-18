from __future__ import annotations

import os
import sys
import threading
import uuid

from .agent_service import complete_drain
from .config import Settings
from .configuration import effective_settings
from .db import Store
from .notifications import deliver_pending, enqueue_reminders
from .scheduler import FairProjectScheduler, ProjectRuntimeRegistry
from .worker import Worker


def run_role(process: str, settings: Settings) -> dict:
    if process == "worker":
        store = Store(settings.storage_target)
        registry = ProjectRuntimeRegistry(settings.managed_projects)
        scheduler = FairProjectScheduler(registry)
        results = []
        for _ in registry.enabled():
            project = scheduler.next_project(registry.enabled())
            if project is None:
                break
            result = Worker(store, settings.for_project(project)).run_once()
            if result is not None:
                results.append(result)
        store.close()
        return {"status": "processed", "results": results} if results else {"status": "idle"}
    elif process == "scheduler":
        store = Store(settings.storage_target)
        recovered = 0
        requeued = 0
        for project in settings.managed_project_ids:
            project_settings = effective_settings(store, settings.for_project(project), project)
            recovered += store.recover_expired(
                project,
                "scheduler",
                project_settings.max_attempts,
                project_settings.item_token_budget,
                project_settings.cheap_profile_max_tokens,
                project_settings.standard_profile_max_tokens,
            )
            requeued += store.requeue_retryable(project, "scheduler", project_settings.max_attempts)
        result = {"recovered": recovered, "requeued": requeued}
        store.close()
        return result
    elif process == "notifications":
        store = Store(settings.storage_target)
        reminders = 0
        delivered = 0
        failed = 0
        for project in settings.managed_project_ids:
            project_settings = effective_settings(store, settings.for_project(project), project)
            if not project_settings.notifications_enabled:
                continue
            if project_settings.in_app_notifications:
                reminders += enqueue_reminders(store, project_settings.review_reminder_days, project)
            delivery = deliver_pending(
                store,
                project_settings.webhook_url,
                project_settings.webhook_secret,
                project=project,
            )
            delivered += delivery["delivered"]
            failed += delivery["failed"]
        result = {"delivered": delivered, "failed": failed, "reminders": reminders}
        store.close()
        return result
    elif process == "reconcile":
        store = Store(settings.storage_target)
        complete_drain(store, "reconciler")
        recovered = 0
        requeued = 0
        completed = 0
        for project in settings.managed_project_ids:
            project_settings = effective_settings(store, settings.for_project(project), project)
            recovered += store.recover_expired(
                project,
                "reconciler",
                project_settings.max_attempts,
                project_settings.item_token_budget,
                project_settings.cheap_profile_max_tokens,
                project_settings.standard_profile_max_tokens,
            )
            requeued += store.requeue_retryable(project, "reconciler", project_settings.max_attempts)
            worker = Worker(store, project_settings, owner=f"reconciler:{project}")
            while worker.run_once() is not None:
                completed += 1
        result = {
            "status": "completed",
            "recovered": recovered,
            "requeued": requeued,
            "processed": completed,
        }
        store.close()
        return result
    else:
        raise ValueError(f"unknown process role: {process}")


def _heartbeat_loop(process: str, settings: Settings, stop: threading.Event, interval: float) -> None:
    process_name = {
        "worker": "worker",
        "scheduler": "scheduler",
        "notifications": "notification",
        "reconcile": "reconciliation",
    }[process]
    instance = f"{process_name}-{uuid.uuid4().hex}"
    store = Store(settings.storage_target)
    try:
        while not stop.is_set():
            store.heartbeat(process_name, instance, details={"project": settings.project})
            if process == "worker":
                store.heartbeat("agent", instance, details={"project": settings.project})
            stop.wait(interval)
    finally:
        store.close()


def main() -> None:
    process = sys.argv[1] if len(sys.argv) > 1 else "worker"
    settings = Settings.from_env()
    once = os.getenv("CLM_PROCESS_ONCE", "false").lower() == "true"
    interval = float(os.getenv("CLM_PROCESS_INTERVAL_SECONDS", "30"))
    if once:
        print(run_role(process, settings))
        return
    if not 1 <= interval <= 45:
        raise ValueError("CLM_PROCESS_INTERVAL_SECONDS must be between 1 and 45")
    stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_loop,
        args=(process, settings, stop, interval),
        daemon=True,
    )
    heartbeat.start()
    try:
        while True:
            print(run_role(process, settings), flush=True)
            stop.wait(interval)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        heartbeat.join(timeout=interval + 5)


if __name__ == "__main__":
    main()
