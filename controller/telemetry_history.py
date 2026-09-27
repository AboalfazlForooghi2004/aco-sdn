from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict
from pathlib import Path

from aco.models import LinkMetrics


class TelemetryHistory:
    """Durable, interval-limited JSONL telemetry snapshots."""

    def __init__(
        self,
        path: str | Path,
        interval_seconds: float,
        max_records: int,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self.max_records = max_records
        self._last_recorded_at: float | None = None
        self._writes_since_compaction = 0
        self._lock = threading.RLock()

    def append(
        self,
        *,
        observed_at: float,
        topology_generation: int,
        metrics: dict[tuple[int, int], LinkMetrics],
    ) -> bool:
        with self._lock:
            if (
                self._last_recorded_at is not None
                and observed_at - self._last_recorded_at
                < self.interval_seconds
            ):
                return False
            record = {
                "schema_version": 1,
                "observed_at": observed_at,
                "topology_generation": topology_generation,
                "links": [
                    {
                        "source": source,
                        "target": target,
                        **asdict(value),
                    }
                    for (source, target), value in sorted(
                        metrics.items()
                    )
                ],
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
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
            self._last_recorded_at = observed_at
            self._writes_since_compaction += 1
            if self._writes_since_compaction >= 100:
                self._compact()
                self._writes_since_compaction = 0
        return True

    def _compact(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as source:
            recent = deque(source, maxlen=self.max_records)
        temporary = self.path.with_suffix(
            self.path.suffix + ".tmp"
        )
        with temporary.open("w", encoding="utf-8") as output:
            output.writelines(recent)
        temporary.replace(self.path)