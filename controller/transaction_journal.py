from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class TransactionJournal:
    """Append-only route-transaction state for restart diagnostics."""

    TERMINAL = {"committed", "rolled_back"}

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def append(
        self,
        *,
        transaction_id: str,
        status: str,
        occurred_at: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "status": status,
            "occurred_at": occurred_at,
            "details": details or {},
        }
        with self._lock:
            self.path.parent.mkdir(
                parents=True, exist_ok=True
            )
            with self.path.open(
                "a", encoding="utf-8"
            ) as destination:
                destination.write(
                    json.dumps(
                        record,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    + "\n"
                )

    def unresolved(self) -> tuple[str, ...]:
        if not self.path.exists():
            return ()
        latest: dict[str, str] = {}
        with self._lock, self.path.open(
            encoding="utf-8"
        ) as source:
            for line in source:
                try:
                    record = json.loads(line)
                    latest[str(record["transaction_id"])] = str(
                        record["status"]
                    )
                except (
                    json.JSONDecodeError,
                    KeyError,
                    TypeError,
                ):
                    continue
        return tuple(
            sorted(
                transaction_id
                for transaction_id, status in latest.items()
                if status not in self.TERMINAL
            )
        )