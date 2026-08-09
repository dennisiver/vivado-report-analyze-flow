# -*- coding: utf-8 -*-
"""Unit tests for the environment baseline (stage 0)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import environment       # noqa: E402
import risk_rules        # noqa: E402


def record(version="2024.2", os_id="rhel", os_version="8.8", host="ws01",
           python_version="3.9.0"):
    return {
        "timestamp": "2026-08-09T10:00:00",
        "vivado": {"version": version, "build": "Build 5239630"},
        "os": {"id": os_id, "version_id": os_version, "pretty_name": "x",
               "kernel": "4.18.0", "supported_by_vivado_2024_2": True},
        "python": {"version": python_version, "executable": "/usr/bin/python3"},
        "host": {"hostname": host},
    }


class TestDetection(unittest.TestCase):
    def test_python_and_host_are_populated(self):
        self.assertTrue(environment.detect_python()["version"])
        self.assertIn("hostname", environment.detect_host())

    def test_os_detection_returns_the_expected_shape(self):
        detected = environment.detect_os()
        for key in ("id", "version_id", "kernel",
                    "supported_by_vivado_2024_2"):
            self.assertIn(key, detected)

    def test_supported_os_matrix(self):
        # Exercised through the module's table rather than the live machine,
        # since the workstation OS is not known in advance.
        self.assertIn("8", environment.SUPPORTED_OS["rhel"])
        self.assertIn("22.04", environment.SUPPORTED_OS["ubuntu"])

    def test_vivado_info_is_read_from_the_tcl_output(self):
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "vivado_version.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"version": "2024.2", "build": "Build 5239630",
                           "install_root": "/tools/Vivado/2024.2"}, handle)
            detected = environment.detect_vivado(path)
            self.assertEqual(detected["version"], "2024.2")
            self.assertEqual(detected["source"], "vivado")
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_missing_vivado_info_is_not_fatal(self):
        detected = environment.detect_vivado("/nonexistent/path.json")
        self.assertEqual(detected["source"], "unavailable")


class TestComparison(unittest.TestCase):
    def test_identical_environment_is_no_change(self):
        comparison = environment.compare_environments(record(), record())
        self.assertFalse(comparison["changed"])
        self.assertFalse(comparison["vivado_changed"])

    def test_first_run_creates_a_baseline_without_alarming(self):
        comparison = environment.compare_environments(None, record())
        self.assertTrue(comparison["first_run"])
        self.assertEqual(comparison["changes"], [])

    def test_vivado_version_change_is_detected(self):
        comparison = environment.compare_environments(
            record(version="2021.2"), record(version="2024.2"))
        self.assertTrue(comparison["vivado_changed"])
        self.assertTrue(comparison["changed"])

    def test_host_change_is_noted_but_is_not_a_vivado_change(self):
        comparison = environment.compare_environments(
            record(host="ws01"), record(host="ws02"))
        self.assertTrue(comparison["changed"])
        self.assertFalse(comparison["vivado_changed"])

    def test_os_change_is_detected(self):
        comparison = environment.compare_environments(
            record(os_version="8.8"), record(os_version="9.3"))
        self.assertTrue(any(c["field"] == "os.version_id"
                            for c in comparison["changes"]))


class TestEnvironmentRiskGrading(unittest.TestCase):
    def test_vivado_change_blocks_signoff_but_not_bringup(self):
        comparison = environment.compare_environments(
            record(version="2021.2"), record(version="2024.2"))
        assessment = risk_rules.evaluate(environment_comparison=comparison)

        found = [f for f in assessment["findings"]
                 if f["id"] == "ENV.VIVADO_CHANGED"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], risk_rules.CRITICAL)
        self.assertTrue(assessment["verdict"]["bringup_ok"])
        self.assertFalse(assessment["verdict"]["signoff_ok"])

    def test_unchanged_environment_produces_no_findings(self):
        comparison = environment.compare_environments(record(), record())
        assessment = risk_rules.evaluate(
            environment_comparison=comparison,
            environment=record())
        self.assertEqual([f for f in assessment["findings"]
                          if f["id"].startswith("ENV.")], [])

    def test_unsupported_os_is_a_warning_not_a_blocker(self):
        unsupported = record()
        unsupported["os"]["supported_by_vivado_2024_2"] = False
        assessment = risk_rules.evaluate(environment=unsupported)

        found = [f for f in assessment["findings"]
                 if f["id"] == "ENV.OS_UNSUPPORTED"][0]
        self.assertEqual(found["severity"], risk_rules.WARNING)
        self.assertTrue(assessment["verdict"]["bringup_ok"])

    def test_unknown_os_support_is_not_reported(self):
        unknown = record()
        unknown["os"]["supported_by_vivado_2024_2"] = None
        assessment = risk_rules.evaluate(environment=unknown)
        self.assertEqual([f for f in assessment["findings"]
                          if f["id"] == "ENV.OS_UNSUPPORTED"], [])


class TestEnvironmentCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_writes_the_baseline_and_compare(self):
        self.assertEqual(
            environment.main(["--outdir", self.dir, "--save",
                              "--vivado-version", "2024.2"]), 0)
        self.assertTrue(os.path.isfile(
            os.path.join(self.dir, "manifests", "environment_current.json")))
        compare = environment.load_json(environment.compare_path(self.dir))
        self.assertTrue(compare["comparison"]["first_run"])

    def test_without_save_no_baseline_is_stored(self):
        environment.main(["--outdir", self.dir, "--vivado-version", "2024.2"])
        self.assertFalse(os.path.isfile(
            os.path.join(self.dir, "manifests", "environment_current.json")))

    def test_second_run_with_a_different_version_reports_the_change(self):
        environment.main(["--outdir", self.dir, "--save",
                          "--vivado-version", "2021.2"])
        environment.main(["--outdir", self.dir, "--save",
                          "--vivado-version", "2024.2"])
        compare = environment.load_json(environment.compare_path(self.dir))
        self.assertTrue(compare["comparison"]["vivado_changed"])


if __name__ == "__main__":
    unittest.main()
