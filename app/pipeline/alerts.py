from dataclasses import asdict, dataclass
from typing import Literal


AlertSeverity = Literal["info", "warning", "danger"]


@dataclass(frozen=True)
class AlertSource:
    processor: str
    camera_devices: tuple[str, ...]

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload["camera_devices"] = list(self.camera_devices)
        return payload


@dataclass(frozen=True)
class AlertEvent:
    event_id: str
    frame_set_id: int
    relay_run_id: int | None
    timestamp_ms: int
    severity: AlertSeverity
    distance_m: float | None
    joint: str | None
    obstacle_id: str | None
    ttl_ms: int
    source: AlertSource

    def to_payload(self) -> dict:
        payload = asdict(self)
        payload["source"] = self.source.to_payload()
        return payload
