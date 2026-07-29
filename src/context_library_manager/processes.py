from __future__ import annotations

import os
import sys
import threading

from .agent_service import complete_drain
from .config import Settings
from .configuration import effective_settings
from .db import Store
from .notifications import deliver_pending, enqueue_reminders
from .worker import Worker


def run_role(process: str, settings: Settings) -> dict:
    if process == "worker":
        store = Store(settings.storage_target)
        result = Worker(store, settings).run_once()
        store.close()
        return result or {"status": "idle"}
    elif process == "scheduler":
        store = Store(settings.storage_target)
        settings = effective_settings(store, settings, settings.project)
        result = {
            "recovered": store.recover_expired(
                settings.project,
                "scheduler",
                settings.max_attempts,
                settings.item_token_budget,
                settings.cheap_profile_max_tokens,
                settings.standard_profile_max_tokens,
            ),
            "requeued": store.requeue_retryable(settings.project, "scheduler", settings.max_attempts),
        }
        store.close()
        return result
    elif process == "notifications":
        store = Store(settings.storage_target)
        settings = effective_settings(store, settings, settings.project)
        reminders = (
            enqueue_reminders(store, settings.review_reminder_days)
            if settings.notifications_enabled and settings.in_app_notifications
            else 0
        )
        result = (
            deliver_pending(
                store,
                settings.webhook_url,
                settings.webhook_secret,
            )
            if settings.notifications_enabled
            else {"delivered": 0, "failed": 0}
        ) | {"reminders": reminders}
        store.close()
        return result
    elif process == "reconcile":
        store = Store(settings.storage_target)
        settings = effective_settings(store, settings, settings.project)
        recovered = store.recover_expired(
            settings.project,
            "reconciler",
            settings.max_attempts,
            settings.item_token_budget,
            settings.cheap_profile_max_tokens,
            settings.standard_profile_max_tokens,
        )
        complete_drain(store, "reconciler")
        requeued = store.requeue_retryable(settings.project, "reconciler", settings.max_attempts)
        completed = 0
        worker = Worker(store, settings, owner="reconciler")
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
    instance = f"{process_name}-{os.getpid()}"
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
