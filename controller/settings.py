from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    poll_interval_seconds: float
    link_capacity_bps: float
    max_age_seconds: float


def load_telemetry_settings(
    path: str | Path | None = None,
) -> TelemetrySettings:
    config_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[1]
        / "config"
        / "config.yaml"
    )
    with config_path.open(encoding="utf-8") as config_file:
        document = yaml.safe_load(config_file)
    telemetry = document["telemetry"]
    return TelemetrySettings(
        poll_interval_seconds=float(
            telemetry["poll_interval_seconds"]
        ),
        link_capacity_bps=float(telemetry["link_capacity_bps"]),
        max_age_seconds=float(telemetry["max_age_seconds"]),
    )