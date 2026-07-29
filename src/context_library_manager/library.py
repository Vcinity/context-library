from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from context_library_maintainer.service import MaintainerApplicationService, MaintainerContext

from .config import Settings
from .contracts import (
    ContentStatus,
    DecisionDetail,
    DecisionSummary,
    LibrarySnapshot,
    Page,
)
from .security import redact_text

CACHE_LIMIT = 64


def redact(value: str) -> tuple[str, bool]:
    safe = redact_text(value)
    return safe, safe != value


@dataclass(frozen=True)
class LibraryError(Exception):
    code: str
    message: str
    status_code: int = 502

    def __str__(self) -> str:
        return self.message


class LibraryReader:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self.maintainer = MaintainerApplicationService(
            MaintainerContext(
                library_root=settings.library_root,
                state_root=settings.state_root,
                project=settings.project,
                actor="manager:library-reader",
            )
        )

    def _cache(self, key: tuple[Any, ...], value: Any) -> None:
        self.cache[key] = value
        self.cache.move_to_end(key)
        while len(self.cache) > CACHE_LIMIT:
            self.cache.popitem(last=False)

    def _cached(self, key: tuple[Any, ...]) -> Any | None:
        value = self.cache.get(key)
        if value is not None:
            self.cache.move_to_end(key)
        return value

    def _invoke(self, *, project: str | None = None, **options: Any) -> dict[str, Any]:
        selected_project = project or self.settings.project
        try:
            return self.maintainer.query(project=selected_project, **options)
        except KeyError as exc:
            raise LibraryError("decision-not-found", "Decision was not found", 404) from exc
        except ValueError as exc:
            raise LibraryError("maintainer-query-failed", str(exc)) from exc

    def snapshot(self, project: str | None = None) -> LibrarySnapshot:
        return LibrarySnapshot.model_validate(self._invoke(project=project, digest_only=True))

    @staticmethod
    def _snapshot(data: dict[str, Any]) -> LibrarySnapshot:
        return LibrarySnapshot.model_validate(
            {name: data[name] for name in ("project", "library_digest", "publication_revision")}
        )

    def search(
        self,
        *,
        query: str = "",
        status: ContentStatus | None = None,
        category: str | None = None,
        page: int = 1,
        page_size: int = 25,
        project: str | None = None,
    ) -> tuple[LibrarySnapshot, Page[DecisionSummary]]:
        selected_project = project or self.settings.project
        data = self._invoke(
            project=selected_project,
            page=page,
            page_size=page_size,
            query=query,
            status=status.value if status else None,
            category=category,
        )
        snapshot = self._snapshot(data)
        key = (
            selected_project,
            snapshot.library_digest,
            query,
            status,
            category,
            page,
            page_size,
        )
        cached = self._cached(key)
        if cached is not None:
            return snapshot, cached
        collection = Page[DecisionSummary].model_validate(
            {name: data[name] for name in ("items", "page", "page_size", "total", "next_page")}
        )
        collection.items = [self._redact_summary(item) for item in collection.items]
        self._cache(key, collection)
        return snapshot, collection

    def detail(self, decision_id: str, project: str | None = None) -> tuple[LibrarySnapshot, DecisionDetail]:
        selected_project = project or self.settings.project
        data = self._invoke(project=selected_project, decision_id=decision_id)
        snapshot = self._snapshot(data)
        key = (selected_project, snapshot.library_digest, "detail", decision_id)
        cached = self._cached(key)
        if cached is not None:
            return snapshot, cached
        decision = self._redact_detail(DecisionDetail.model_validate(data["decision"]))
        self._cache(key, decision)
        return snapshot, decision

    @staticmethod
    def _redact_summary(decision: DecisionSummary) -> DecisionSummary:
        updates = {}
        for field in ("subject", "decision", "rationale"):
            updates[field] = redact(getattr(decision, field))[0]
        return decision.model_copy(update=updates)

    @classmethod
    def _redact_detail(cls, decision: DecisionDetail) -> DecisionDetail:
        summary = cls._redact_summary(decision)
        decisionmaker, _ = redact(decision.decisionmaker)
        sources = []
        for source in decision.sources:
            uri, uri_redacted = redact(source.uri)
            label, label_redacted = redact(source.label)
            was_redacted = source.redacted or uri_redacted or label_redacted
            sources.append(
                source.model_copy(
                    update={
                        "uri": uri,
                        "label": label,
                        "redacted": was_redacted,
                        "secret_state": "redacted" if was_redacted else source.secret_state,
                    }
                )
            )
        return decision.model_copy(
            update={
                "subject": summary.subject,
                "decision": summary.decision,
                "rationale": summary.rationale,
                "decisionmaker": decisionmaker,
                "sources": sources,
            }
        )
