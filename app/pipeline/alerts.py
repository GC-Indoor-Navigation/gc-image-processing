import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal, Protocol

import httpx


LOGGER = logging.getLogger("app.pipeline.alerts")


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


AlertSender = Callable[[str, dict, float], None]


class ProximityAlertEvaluator(Protocol):
    def evaluate(
        self,
        *,
        processing_result: Any,
        skeleton_result: Any | None,
        ttl_ms: int,
        processor_name: str,
        camera_devices: tuple[str, ...],
    ) -> AlertEvent | None:
        ...


class NoOpProximityAlertEvaluator:
    def evaluate(
        self,
        *,
        processing_result: Any,
        skeleton_result: Any | None,
        ttl_ms: int,
        processor_name: str,
        camera_devices: tuple[str, ...],
    ) -> AlertEvent | None:
        return None


class AlertPublisher:
    def __init__(
        self,
        *,
        enabled: bool = False,
        target_url: str = "",
        timeout_sec: float = 1.0,
        sender: AlertSender | None = None,
    ):
        self.enabled = enabled
        self.target_url = target_url
        self.timeout_sec = timeout_sec
        self.sender = sender or _httpx_alert_sender
        self.sent_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.last_event_id: str | None = None
        self.last_error: str | None = None

    def publish(self, event: AlertEvent) -> bool:
        self.last_event_id = event.event_id
        if not self.enabled:
            self.skipped_count += 1
            self.last_error = None
            return False
        if not self.target_url:
            self.failed_count += 1
            self.last_error = "alert target url is not configured"
            LOGGER.warning("alert publish skipped: %s", self.last_error)
            return False
        try:
            self.sender(self.target_url, event.to_payload(), self.timeout_sec)
        except Exception as exc:
            self.failed_count += 1
            self.last_error = str(exc)
            LOGGER.exception("alert publish failed event_id=%s", event.event_id)
            return False

        self.sent_count += 1
        self.last_error = None
        return True

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "target_configured": bool(self.target_url),
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "last_event_id": self.last_event_id,
            "last_error": self.last_error,
        }


def _httpx_alert_sender(target_url: str, payload: dict, timeout_sec: float) -> None:
    response = httpx.post(target_url, json=payload, timeout=timeout_sec)
    response.raise_for_status()
