# -*- coding: utf-8 -*-
"""Stages 0-2 report together; stage 3 is gated on what they found."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "python"))

import flow_summary as flow_mod  # noqa: E402
import precheck                  # noqa: E402


class PrecheckCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        os.makedirs(os.path.join(self.dir, "manifests"))

    def write(self, relative, data):
        path = os.path.join(self.dir, relative)
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def clean_filelist(self):
        return {"available": True, "missing_files": [], "parse_errors": [],
                "files": ["rtl/top.v"], "incdirs": [], "missing_incdirs": [],
                "file_count": 1, "reconcile": {"consistent": True,
                                               "only_in_filelist": [],
                                               "only_in_project": []}}

    def broken_filelist(self):
        result = self.clean_filelist()
        result["missing_files"] = ["rtl/absent.v"]
        return result

    def clean_preflight(self):
        return {"run": "impl_1", "errors": [], "warnings": [],
                "changed": False, "launched": False}

    def run_precheck(self, **statuses):
        collected = precheck.collect(self.dir, "impl_1", statuses)
        combined = flow_mod.combine(collected)
        allowed, reasons = precheck.gate(collected, combined)
        return collected, combined, allowed, reasons


class TestGate(PrecheckCase):

    def test_clean_stages_let_elaboration_run(self):
        self.write("manifests/filelist_check.json", self.clean_filelist())
        self.write("manifests/preflight_impl_1.json", self.clean_preflight())
        self.write("manifests/environment_compare.json",
                   {"environment": {}, "comparison": {"changed": False}})

        _, _, allowed, reasons = self.run_precheck(env=0, files=0, project=0)
        self.assertTrue(allowed, reasons)

    def test_a_blocker_stops_elaboration_even_when_every_stage_exited_zero(self):
        """The point of the gate.

        Stages exit 0 with BLOCKERs by design, so gating on status alone would
        spend five minutes elaborating a design already known to be broken.
        """
        self.write("manifests/filelist_check.json", self.broken_filelist())
        self.write("manifests/preflight_impl_1.json", self.clean_preflight())

        _, combined, allowed, reasons = self.run_precheck(
            env=0, files=0, project=0)
        self.assertFalse(combined["bringup_ok"])
        self.assertFalse(allowed)
        self.assertTrue(any("BLOCKER" in reason for reason in reasons))

    def test_a_failed_stage_stops_elaboration(self):
        self.write("manifests/filelist_check.json", self.clean_filelist())
        self.write("manifests/preflight_impl_1.json", self.clean_preflight())

        _, _, allowed, reasons = self.run_precheck(env=0, files=2, project=0)
        self.assertFalse(allowed)
        self.assertTrue(any("狀態 2" in reason for reason in reasons))

    def test_warnings_alone_do_not_stop_elaboration(self):
        """A changed Vivado version is CRITICAL, not a reason to stop building."""
        self.write("manifests/filelist_check.json", self.clean_filelist())
        self.write("manifests/preflight_impl_1.json", self.clean_preflight())
        self.write("manifests/environment_compare.json",
                   {"environment": {},
                    "comparison": {"changed": True, "vivado_changed": True,
                                   "changes": [{"field": "vivado.version",
                                                "from": "2021.2",
                                                "to": "2024.2"}]}})

        _, combined, allowed, reasons = self.run_precheck(
            env=0, files=0, project=0)
        self.assertFalse(combined["signoff_ok"])
        self.assertTrue(allowed, reasons)


class TestNothingIsQuietlySkipped(PrecheckCase):

    def test_skipped_stage_blocks_and_is_named(self):
        self.write("manifests/filelist_check.json", self.clean_filelist())

        collected, combined, allowed, reasons = self.run_precheck(
            env=2, files=0, project=precheck.SKIPPED)
        self.assertFalse(allowed)
        self.assertTrue(any("未執行" in reason for reason in reasons))
        # flow_summary already knows a stage that did not run cannot clear
        # sign-off; this asserts precheck feeds it the right shape.
        self.assertFalse(combined["signoff_ok"])
        self.assertIn("project", combined["stages_not_run"])

    def test_stage_with_no_artifact_is_reported_not_assumed_clean(self):
        collected, _, allowed, reasons = self.run_precheck(
            env=0, files=0, project=0)
        self.assertFalse(allowed)
        self.assertTrue(any("沒有留下可判讀的結果" in reason
                            for reason in reasons))
        self.assertTrue(all(not entry["ran"] for entry in collected))

    def test_report_names_the_command_that_would_fill_the_gap(self):
        collected, combined, allowed, reasons = self.run_precheck(
            env=0, files=0, project=0)
        text = precheck.render(collected, combined, allowed, reasons)
        self.assertIn("make check-project", text)
        self.assertIn("未經檢查不等於沒問題", text)


class TestCheapStagesAllReport(PrecheckCase):
    """Fail-fast is wrong for the seconds-long stages: report them together."""

    def test_problems_in_two_stages_both_appear(self):
        self.write("manifests/filelist_check.json", self.broken_filelist())
        self.write("manifests/environment_compare.json",
                   {"environment": {},
                    "comparison": {"changed": True, "vivado_changed": True,
                                   "changes": [{"field": "vivado.version",
                                                "from": "2021.2",
                                                "to": "2024.2"}]}})
        self.write("manifests/preflight_impl_1.json", self.clean_preflight())

        collected, combined, allowed, reasons = self.run_precheck(
            env=0, files=1, project=0)
        text = precheck.render(collected, combined, allowed, reasons)

        self.assertIn("Vivado 版本已改變", text)
        self.assertIn("不存在的檔案", text)
        self.assertFalse(allowed)


class TestPrefersTheStageWrittenAssessment(PrecheckCase):

    def test_risk_files_json_is_used_when_present(self):
        """One evaluation, written by the stage, read by everything after it."""
        self.write("manifests/filelist_check.json", self.clean_filelist())
        self.write("risk_files.json", {
            "findings": [{"id": "MADE.UP", "severity": "BLOCKER",
                          "blocks_bringup": True, "blocks_signoff": True,
                          "title": "來自 risk_files.json", "risk": "r",
                          "action": "a"}],
            "verdict": {"counts": {"BLOCKER": 1, "CRITICAL": 0,
                                   "WARNING": 0, "INFO": 0},
                        "blockers": 1, "criticals": 0, "waived": 0,
                        "bringup_ok": False, "signoff_ok": False,
                        "bringup_blocker_count": 1,
                        "signoff_blocker_count": 1}})

        collected, combined, allowed, _ = self.run_precheck(
            env=0, files=0, project=0)
        titles = [item["title"] for item in combined["blockers"]]
        self.assertIn("來自 risk_files.json", titles)
        self.assertFalse(allowed)


class TestJsonForTheBanner(PrecheckCase):

    def test_written_json_carries_a_verdict_the_banner_can_read(self):
        import banner
        import io
        self.write("manifests/filelist_check.json", self.broken_filelist())
        original = sys.stdout
        sys.stdout = io.StringIO()
        try:
            precheck.main(["--outdir", self.dir, "--run", "impl_1",
                           "--status", "env=0", "--status", "files=0",
                           "--status", "project=skipped"])
        finally:
            sys.stdout = original

        loaded = banner.load_verdict(os.path.join(self.dir, "precheck.json"))
        self.assertIsInstance(loaded, dict)
        self.assertEqual(banner.classify(0, loaded)[0], banner.FAIL)


class TestStatusParsing(unittest.TestCase):

    def test_numbers_and_the_skipped_sentinel(self):
        self.assertEqual(precheck._parse_status("files=2"), ("files", 2))
        self.assertEqual(precheck._parse_status("project=skipped"),
                         ("project", precheck.SKIPPED))

    def test_rejects_nonsense(self):
        import argparse
        for bad in ("files", "nosuchstage=0", "files=maybe"):
            self.assertRaises(argparse.ArgumentTypeError,
                              precheck._parse_status, bad)


if __name__ == "__main__":
    unittest.main()
