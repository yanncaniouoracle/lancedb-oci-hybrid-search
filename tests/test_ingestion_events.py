"""Regression tests for the OCI Object Storage event normalization layer."""

from __future__ import annotations

import unittest

from oci_lancedb_hybrid.ingestion_events import parse_object_event, route_event


def object_event(event_type: str, key: str = "images/sample.bin") -> dict:
    """OCI's documented Object Storage event structure, reduced to used fields."""
    return {
        "eventID": "event-123",
        "eventType": event_type,
        "eventTime": "2026-10-01T10:00:00Z",
        "data": {
            "resourceName": key,
            "additionalDetails": {
                "bucketName": "lancedb-cold",
                "eTag": "etag-123",
            },
        },
    }


class ObjectStorageEventTests(unittest.TestCase):
    def test_create_event_is_normalized_and_uses_most_specific_route(self) -> None:
        event = parse_object_event(object_event("com.oraclecloud.objectstorage.createobject"))
        route = route_event(event, [
            {"bucket": "lancedb-cold", "prefix": "", "table": "all_objects"},
            {"bucket": "lancedb-cold", "prefix": "images/", "table": "image_vectors"},
        ])

        self.assertEqual(event.operation, "upsert")
        self.assertEqual(event.source_uri, "s3://lancedb-cold/images/sample.bin")
        self.assertEqual(event.object_version, "etag-123")
        self.assertEqual(route["table"], "image_vectors")

    def test_delete_event_is_normalized(self) -> None:
        event = parse_object_event(object_event("com.oraclecloud.objectstorage.deleteobject"))

        self.assertEqual(event.operation, "delete")
        self.assertEqual(event.asset_id, "lancedb-cold/images/sample.bin")

    def test_unrouted_bucket_is_ignored(self) -> None:
        event = parse_object_event(object_event("com.oraclecloud.objectstorage.updateobject"))

        self.assertIsNone(route_event(event, [{"bucket": "other", "prefix": "", "table": "other"}]))


if __name__ == "__main__":
    unittest.main()
