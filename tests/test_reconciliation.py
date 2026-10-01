import unittest

from oci_lancedb_hybrid.reconciliation import _message


class ReconciliationMessageTests(unittest.TestCase):
    def test_messages_have_stable_idempotency_key(self):
        route = {"bucket": "raw", "prefix": "logs/", "table": "objects"}
        first = _message("upsert", route, "logs/a.txt", "etag-1", "2026-10-01T00:00:00Z")
        second = _message("upsert", route, "logs/a.txt", "etag-1", "later")
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["schema"], "oci-lancedb-reconciliation/v1")

    def test_changed_version_has_a_different_idempotency_key(self):
        route = {"bucket": "raw", "prefix": "", "table": "objects"}
        first = _message("upsert", route, "a.txt", "etag-1")
        second = _message("upsert", route, "a.txt", "etag-2")
        self.assertNotEqual(first["event_id"], second["event_id"])
