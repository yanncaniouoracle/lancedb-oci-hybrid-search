"""Single active queue consumer that fans normalized events out to every replica."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

import oci

from .ingestion_events import parse_object_event, route_event


def queue_client():
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    return oci.queue.QueueClient(
        config={}, signer=signer, service_endpoint=os.environ["LANCEDB_INGESTION_QUEUE_ENDPOINT"]
    )


def post_json(endpoint: str, value: dict) -> None:
    request = Request(
        f"{endpoint.rstrip('/')}/v1/ingest",
        data=json.dumps(value).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Worker {endpoint} returned HTTP {response.status}")


def normalized_payload(message_content: dict, message_id: str, routes: list[dict]) -> dict | None:
    """Return a worker payload from either an OCI event or a reconciler message."""
    if message_content.get("schema") == "oci-lancedb-reconciliation/v1":
        required = {"operation", "bucket", "object_key", "target_table"}
        missing = required - message_content.keys()
        if missing:
            raise ValueError(f"Reconciliation message is missing {sorted(missing)!r}")
        return {
            "event_id": str(message_content.get("event_id") or message_id),
            "operation": message_content["operation"],
            "bucket": message_content["bucket"],
            "object_key": message_content["object_key"],
            "object_version": message_content.get("object_version"),
            "event_time": message_content.get("event_time"),
            "target_table": message_content["target_table"],
        }

    event = parse_object_event(message_content)
    route = route_event(event, routes)
    if route is None:
        return None
    return {
        "event_id": event.event_id or message_id,
        "operation": event.operation,
        "bucket": event.bucket,
        "object_key": event.object_key,
        "object_version": event.object_version,
        "event_time": event.event_time,
        "target_table": route["table"],
    }


def main() -> None:
    queue_id = os.environ["LANCEDB_INGESTION_QUEUE_ID"]
    routes = json.loads(Path(os.environ["LANCEDB_SOURCE_ROUTES_PATH"]).read_text())
    workers = json.loads(os.environ["LANCEDB_INGESTION_WORKER_ENDPOINTS"])
    client = queue_client()
    while True:
        response = client.get_messages(
            queue_id,
            timeout_in_seconds=20,
            visibility_in_seconds=90,
            limit=10,
        )
        for message in response.data.messages:
            try:
                payload = normalized_payload(json.loads(message.content), message.id, routes)
                if payload is not None:
                    for worker in workers:
                        post_json(worker, payload)
                client.delete_message(queue_id, message.receipt)
            except Exception:
                # Leave the message visible again after the queue visibility timeout.
                # A production revision should add metrics and a DLQ alert on repeated failure.
                continue
        time.sleep(0.1)
