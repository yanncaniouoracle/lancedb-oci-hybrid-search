"""Reconcile Object Storage routes with the local LanceDB hot-table replica.

OCI Events provide low-latency, at-least-once deltas.  This command is the
correctness backstop: it scans Object Storage and enqueues only the missing,
changed, and stale keys through the normal Queue/controller/worker path.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ingestion_events import ObjectEvent, route_event

if TYPE_CHECKING:
    import lancedb


SCHEMA = "oci-lancedb-reconciliation/v1"


def _iso8601(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value or "")


def _object_key_from_id(asset_id: str, bucket: str) -> str | None:
    prefix = f"{bucket}/"
    return asset_id[len(prefix):] if asset_id.startswith(prefix) else None


def _message(operation: str, route: dict, key: str, version: str | None = None,
             event_time: str | None = None) -> dict[str, str | None]:
    unique = f"{operation}\0{route['bucket']}\0{key}\0{version or ''}"
    return {
        "schema": SCHEMA,
        "event_id": f"reconcile-{hashlib.sha256(unique.encode()).hexdigest()}",
        "operation": operation,
        "bucket": str(route["bucket"]),
        "object_key": key,
        "object_version": version,
        "event_time": event_time,
        "target_table": str(route["table"]),
    }


def route_objects(client: Any, namespace: str, route: dict, routes: list[dict]) -> dict[str, tuple[str, str]]:
    """Return key -> (ETag, time) for exactly the keys owned by this route."""
    page: str | None = None
    result: dict[str, tuple[str, str]] = {}
    while True:
        response = client.list_objects(
            namespace,
            route["bucket"],
            prefix=str(route.get("prefix", "")),
            fields="name,etag,timeCreated",
            page=page,
        )
        for item in response.data.objects:
            event = ObjectEvent("reconcile", "upsert", route["bucket"], item.name, item.etag, _iso8601(item.time_created))
            if route_event(event, routes) == route:
                result[item.name] = (item.etag or "", _iso8601(item.time_created))
        page = response.headers.get("opc-next-page")
        if not page:
            return result


def table_rows(database: Any, route: dict, routes: list[dict]) -> dict[str, str]:
    """Return route-owned key -> stored ETag from the controller-node replica."""
    if route["table"] not in database.table_names():
        return {}
    rows: dict[str, str] = {}
    for row in database.open_table(route["table"]).to_arrow().to_pylist():
        key = _object_key_from_id(str(row.get("id", "")), str(route["bucket"]))
        if key is None:
            continue
        event = ObjectEvent("reconcile", "upsert", route["bucket"], key, None, None)
        if route_event(event, routes) == route:
            rows[key] = str(row.get("object_version") or "")
    return rows


def reconcile_route(client: Any, namespace: str, database: Any,
                    route: dict, routes: list[dict]) -> list[dict]:
    objects = route_objects(client, namespace, route, routes)
    indexed = table_rows(database, route, routes)
    changes: list[dict] = []
    for key, (etag, created) in objects.items():
        if indexed.get(key) != etag:
            changes.append(_message("upsert", route, key, etag, created))
    for key in indexed.keys() - objects.keys():
        changes.append(_message("delete", route, key))
    return changes


def enqueue(client: Any, queue_id: str, messages: Iterable[dict]) -> int:
    import oci

    batch: list[Any] = []
    count = 0
    for message in messages:
        batch.append(oci.queue.models.PutMessagesDetailsEntry(content=json.dumps(message)))
        if len(batch) == 10:
            client.put_messages(queue_id, oci.queue.models.PutMessagesDetails(messages=batch))
            count += len(batch)
            batch = []
    if batch:
        client.put_messages(queue_id, oci.queue.models.PutMessagesDetails(messages=batch))
        count += len(batch)
    return count


def main() -> None:
    import lancedb
    import oci
    from .ingestion_controller import queue_client

    routes = json.loads(Path(os.environ["LANCEDB_SOURCE_ROUTES_PATH"]).read_text())
    signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    storage = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
    namespace = os.environ.get("OCI_NAMESPACE") or storage.get_namespace().data
    database = lancedb.connect(os.environ["LANCEDB_URI"])
    changes = [change for route in routes for change in reconcile_route(storage, namespace, database, route, routes)]
    count = enqueue(queue_client(), os.environ["LANCEDB_INGESTION_QUEUE_ID"], changes)
    print(json.dumps({"routes": len(routes), "enqueued": count, "changes": changes}, default=str))


if __name__ == "__main__":
    main()
