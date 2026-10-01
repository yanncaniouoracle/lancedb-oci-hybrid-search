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
                event = parse_object_event(json.loads(message.content))
                route = route_event(event, routes)
                if route is not None:
                    payload = {
                        "event_id": event.event_id or message.id,
                        "operation": event.operation,
                        "bucket": event.bucket,
                        "object_key": event.object_key,
                        "object_version": event.object_version,
                        "event_time": event.event_time,
                        "target_table": route["table"],
                    }
                    for worker in workers:
                        post_json(worker, payload)
                client.delete_message(queue_id, message.receipt)
            except Exception:
                # Leave the message visible again after the queue visibility timeout.
                # A production revision should add metrics and a DLQ alert on repeated failure.
                continue
        time.sleep(0.1)
