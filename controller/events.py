from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    occurred_at: float
    category: str
    severity: str
    title: str
    details: dict[str, Any]


class EventTimeline:
    """Bounded in-memory timeline with durable JSONL append log."""

    def __init__(
        self,
        path: str | Path,
        max_events: int = 1000,
    ) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self.path = Path(path)
        self.max_events = max_events
        self._lock = threading.RLock()
        self._events: deque[EventRecord] = deque(
            maxlen=max_events
        )
        self._load_existing()

    def _load_existing(self) -> None:
        if not self.path.exists():
            return
        loaded = deque(maxlen=self.max_events)
        with self.path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    item = json.loads(line)
                    loaded.append(EventRecord(**item))
                except (
                    json.JSONDecodeError,
                    TypeError,
                    KeyError,
                ):
                    continue
        self._events = loaded

    def append(
        self,
        *,
        occurred_at: float,
        category: str,
        severity: str,
        title: str,
        details: dict[str, Any] | None = None,
    ) -> EventRecord:
        event = EventRecord(
            event_id=uuid.uuid4().hex,
            occurred_at=occurred_at,
            category=category,
            severity=severity,
            title=title,
            details=details or {},
        )
        serialized = json.dumps(
            asdict(event),
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._lock:
            self.path.parent.mkdir(
                parents=True, exist_ok=True
            )
            with self.path.open(
                "a", encoding="utf-8"
            ) as destination:
                destination.write(serialized + "\n")
                destination.flush()
            self._events.append(event)
        return event

    def recent(
        self,
        limit: int | None = None,
        category: str | None = None,
    ) -> tuple[EventRecord, ...]:
        with self._lock:
            values = tuple(self._events)
        if category is not None:
            values = tuple(
                item
                for item in values
                if item.category == category
            )
        if limit is not None:
            if limit < 0:
                raise ValueError("limit cannot be negative")
            values = values[-limit:] if limit else ()
        return values