import json
import unittest
from unittest.mock import patch

from backend import observability


class HealthSnapshotTests(unittest.TestCase):
    def test_repeated_health_checks_do_not_accumulate_previous_metrics(self):
        with (
            patch.object(observability, "_stats", observability.RuntimeStats()),
            patch.object(observability, "check_disk", return_value={"ok": True}),
            patch.object(observability, "check_tcp_connect", return_value={"ok": True}),
            patch.object(observability, "check_http", return_value={"ok": True}),
        ):
            for _ in range(1200):
                health = observability.deep_health()

            self.assertTrue(health["ok"])
            self.assertNotIn("metrics", health["metrics"]["last_health"])
            metrics = observability.metrics_snapshot()
            self.assertEqual(metrics["last_health"]["checks"], health["checks"])
            self.assertLess(len(json.dumps(health)), 3000)
            self.assertLess(len(json.dumps(metrics)), 3000)


if __name__ == "__main__":
    unittest.main()
