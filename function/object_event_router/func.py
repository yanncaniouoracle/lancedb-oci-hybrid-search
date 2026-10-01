"""OCI Function: forward one Object Storage CloudEvent to OCI Queue."""

from __future__ import annotations

import io
import json
import os

import oci
from fdk import response


def handler(ctx, data: io.BytesIO | None = None):
    payload = json.loads((data.getvalue() if data else b"{}").decode("utf-8"))
    signer = oci.auth.signers.get_resource_principals_signer()
    client = oci.queue.QueueClient(
        config={}, signer=signer, service_endpoint=os.environ["QUEUE_ENDPOINT"]
    )
    result = client.put_messages(
        os.environ["QUEUE_ID"],
        oci.queue.models.PutMessagesDetails(messages=[
            oci.queue.models.PutMessagesDetailsEntry(content=json.dumps(payload))
        ]),
    )
    failed = [entry for entry in result.data.entries if entry.error]
    if failed:
        raise RuntimeError(f"Queue publish failed: {failed[0].error.code}")
    return response.Response(ctx, response_data=json.dumps({"status": "queued"}), status_code=202)
