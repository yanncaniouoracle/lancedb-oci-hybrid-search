"""Normalization and routing for OCI Object Storage change events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CREATE_OR_UPDATE = {
    "com.oraclecloud.objectstorage.createobject",
    "com.oraclecloud.objectstorage.updateobject",
}
DELETE = "com.oraclecloud.objectstorage.deleteobject"


@dataclass(frozen=True)
class ObjectEvent:
    event_id: str
    operation: str
    bucket: str
    object_key: str
    object_version: str | None
    event_time: str | None

    @property
    def asset_id(self) -> str:
        return f"{self.bucket}/{self.object_key}"

    @property
    def source_uri(self) -> str:
        return f"s3://{self.bucket}/{self.object_key}"


def parse_object_event(payload: dict[str, Any]) -> ObjectEvent:
    event_type = payload.get("eventType") or payload.get("type")
    if event_type not in CREATE_OR_UPDATE | {DELETE}:
        raise ValueError(f"Unsupported Object Storage event type: {event_type!r}")
    data = payload.get("data") or {}
    details = data.get("additionalDetails") or {}
    bucket = details.get("bucketName")
    object_key = data.get("resourceName")
    if not bucket or not object_key:
        raise ValueError("Object Storage event is missing bucketName or resourceName")
    return ObjectEvent(
        event_id=str(payload.get("eventID") or payload.get("id") or ""),
        operation="delete" if event_type == DELETE else "upsert",
        bucket=str(bucket),
        object_key=str(object_key),
        object_version=details.get("versionId") or details.get("eTag"),
        event_time=payload.get("eventTime") or payload.get("time"),
    )


def route_event(event: ObjectEvent, routes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select exactly one route using a bucket match and longest prefix match."""
    matches = [
        route for route in routes
        if route.get("bucket") == event.bucket
        and event.object_key.startswith(str(route.get("prefix", "")))
    ]
    if not matches:
        return None
    return max(matches, key=lambda route: len(str(route.get("prefix", ""))))
