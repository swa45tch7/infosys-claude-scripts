#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from engine import (  # noqa: E402
    PortalSnapshot,
    exit_code,
    load_standards,
    run_checks,
    worst_severity,
)


def load_fixture() -> tuple[PortalSnapshot, dict, float]:
    raw = json.loads((HERE / "tests/fixtures/portal_snapshot.json").read_text(encoding="utf-8"))
    snap = PortalSnapshot(
        devices=raw["devices"],
        device_properties=raw["device_properties"],
        collectors=raw["collectors"],
        alerts=raw["alerts"],
        alert_rules=raw["alert_rules"],
        chains=raw["chains"],
        sdts=raw["sdts"],
        tokens=raw["tokens"],
        api_ok=True,
        portal=raw["portal"],
    )
    return snap, load_standards(), float(raw["now"])


class StandardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snap, self.standards, self.now = load_fixture()
        self.results = {r.check_id: r for r in run_checks(self.snap, self.standards, "all", self.now)}

    def _severities(self, check_id: str) -> dict[str, str]:
        return {f.subject: f.severity for f in self.results[check_id].findings}

    def test_dead_device_is_critical(self) -> None:
        sev = self._severities("device_status")
        self.assertEqual(sev["ACME-SW-01"], "critical")
        self.assertEqual(sev["ACME-APP-01"], "ok")

    def test_down_collector_and_capacity(self) -> None:
        status = self._severities("collector_status")
        self.assertEqual(status["lm-collector-mum-02"], "critical")
        self.assertEqual(status["lm-collector-mum-01"], "ok")
        capacity = self._severities("collector_capacity")
        self.assertEqual(capacity["lm-collector-mum-01"], "warn")
        self.assertEqual(capacity["lm-collector-mum-02"], "critical")

    def test_failover_and_version_drift(self) -> None:
        failover = self._severities("collector_failover")
        self.assertEqual(failover["lm-collector-mum-02"], "critical")
        self.assertEqual(failover["lm-collector-mum-01"], "ok")
        versions = self._severities("collector_versions")
        self.assertEqual(versions["lm-collector-mum-02"], "critical")

    def test_property_and_credential_deviations(self) -> None:
        props = {f.subject: f for f in self.results["property_coverage"].findings}
        self.assertEqual(props["ACME-SW-01"].severity, "critical")
        self.assertIn("location", props["ACME-SW-01"].finding)
        creds = {f.subject: f for f in self.results["category_credentials"].findings}
        self.assertIn("ACME-SW-01", creds)
        self.assertIn("snmp.community", creds["ACME-SW-01"].finding)

    def test_ungrouped_duplicates_alerting_sdt(self) -> None:
        ungrouped = self._severities("ungrouped_devices")
        self.assertEqual(ungrouped["ACME-SW-01"], "warn")
        self.assertNotIn("ACME-APP-01", ungrouped)
        dup_subjects = [f.subject for f in self.results["duplicates"].findings]
        self.assertEqual(dup_subjects, ["10.0.0.10"])
        disabled = self._severities("alerting_disabled")
        self.assertEqual(disabled["ACME-SW-01"], "warn")
        sdt = self._severities("sdt_audit")
        self.assertEqual(sdt["ACME-SW-01"], "critical")

    def test_routing_and_ack_sla(self) -> None:
        routing = {f.subject: f for f in self.results["notification_routing"].findings}
        self.assertEqual(routing["Orphan rule"].severity, "critical")
        self.assertEqual(routing["Critical default"].severity, "ok")
        self.assertIn("priority 100", routing)
        ack = self.results["alert_ack_sla"].findings
        self.assertTrue(ack)
        self.assertEqual(ack[0].severity, "critical")

    def test_idle_token_truncated(self) -> None:
        token = self.results["api_token_hygiene"].findings[0]
        self.assertEqual(token.severity, "warn")
        self.assertTrue(str(token.details["access_id"]).endswith("…"))
        self.assertNotIn("XYZ", json.dumps(token.as_dict()))

    def test_exit_code_critical(self) -> None:
        results = list(self.results.values())
        self.assertEqual(worst_severity(results), "critical")
        self.assertEqual(exit_code(results), 1)

    def test_healthy_snapshot_exits_zero(self) -> None:
        healthy = PortalSnapshot(
            devices=[self.snap.devices[0]],
            device_properties=[self.snap.device_properties[0]],
            collectors=[self.snap.collectors[0]],
            alerts=[],
            alert_rules=[self.snap.alert_rules[0]],
            chains=[self.snap.chains[0]],
            sdts=[],
            tokens=[
                {
                    "id": 1,
                    "adminName": "ops",
                    "accessId": "KEEPSECRET",
                    "status": "active",
                    "lastUsedOn": self.now - 86400,
                }
            ],
            api_ok=True,
            portal="example.logicmonitor.com",
        )
        results = run_checks(healthy, self.standards, "all", self.now)
        self.assertEqual(exit_code(results), 0)
        self.assertNotEqual(worst_severity(results), "critical")


if __name__ == "__main__":
    unittest.main()
