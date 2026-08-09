# -*- coding: utf-8 -*-
"""Unit tests for the cross-stage overview and the human sign-off report."""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import flow_summary      # noqa: E402
import risk_rules        # noqa: E402
import signoff_report    # noqa: E402


def assessment_with(findings):
    return {"findings": findings, "verdict": risk_rules.summarise(findings),
            "waivers": {"applied": [], "expired": []}}


def blocker(identifier="X.BLOCK", title="a blocker"):
    return risk_rules.finding(identifier, title, "risk", "action",
                              blocks_bringup=True, blocks_signoff=True)


def critical(identifier="X.CRIT", title="a critical"):
    return risk_rules.finding(identifier, title, "risk", "action",
                              blocks_signoff=True)


class StageCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_stage(self, stage, findings):
        path = os.path.join(self.dir, "risk_{0}.json".format(stage))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(assessment_with(findings), handle, ensure_ascii=False)
        return path

    def write_json(self, relative, data):
        path = os.path.join(self.dir, relative)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        return path


class TestFlowSummary(StageCase):
    def test_clean_stages_pass_both_verdicts(self):
        self.write_stage("synth_1", [])
        self.write_stage("impl_1", [])
        combined = flow_summary.combine(
            flow_summary.collect(self.dir, ["synth_1", "impl_1"]))
        self.assertTrue(combined["bringup_ok"])
        self.assertTrue(combined["signoff_ok"])

    def test_one_bad_stage_fails_the_whole_flow(self):
        self.write_stage("synth_1", [blocker()])
        self.write_stage("impl_1", [])
        combined = flow_summary.combine(
            flow_summary.collect(self.dir, ["synth_1", "impl_1"]))
        self.assertFalse(combined["bringup_ok"])
        self.assertEqual(len(combined["blockers"]), 1)
        self.assertEqual(combined["blockers"][0]["stage"], "synth_1")

    def test_a_stage_that_did_not_run_cannot_clear_signoff(self):
        self.write_stage("impl_1", [])
        combined = flow_summary.combine(
            flow_summary.collect(self.dir, ["synth_1", "impl_1"]))
        self.assertFalse(combined["signoff_ok"])
        self.assertEqual(combined["stages_not_run"], ["synth_1"])

    def test_flow_level_findings_are_not_counted_twice(self):
        # The file list and the environment are evaluated identically in every
        # stage; listing them per stage would overstate the damage.
        shared = blocker("FILELIST.MISSING_FILE", "file list 引用了 1 個不存在的檔案")
        self.write_stage("synth_1", [shared])
        self.write_stage("impl_1", [dict(shared), blocker("CDC.CRITICAL", "cdc")])

        combined = flow_summary.combine(
            flow_summary.collect(self.dir, ["synth_1", "impl_1"]))
        ids = [b["id"] for b in combined["blockers"]]
        self.assertEqual(sorted(ids), ["CDC.CRITICAL", "FILELIST.MISSING_FILE"])
        # Credited to the earliest stage that could have caught it.
        first = [b for b in combined["blockers"]
                 if b["id"] == "FILELIST.MISSING_FILE"][0]
        self.assertEqual(first["stage"], "synth_1")

    def test_waived_blockers_are_left_out_of_the_action_list(self):
        waived = blocker()
        waived["waived"] = True
        self.write_stage("impl_1", [waived])
        combined = flow_summary.combine(
            flow_summary.collect(self.dir, ["impl_1"]))
        self.assertEqual(combined["blockers"], [])

    def test_overview_stays_short_enough_to_read(self):
        self.write_stage("synth_1", [blocker("A.{0}".format(i), "t{0}".format(i))
                                     for i in range(8)])
        self.write_stage("impl_1", [critical("B.{0}".format(i), "u{0}".format(i))
                                    for i in range(8)])
        self.assertEqual(flow_summary.main(
            ["--outdir", self.dir, "--stages", "synth_1", "impl_1"]), 0)

        with open(os.path.join(self.dir, "latest_flow.md"),
                  encoding="utf-8") as handle:
            text = handle.read()
        self.assertLess(len(text.splitlines()), 60)
        self.assertIn("合成流程總覽", text)


class TestSignoffReport(StageCase):
    def test_checklist_covers_every_defined_row(self):
        self.write_stage("impl_1", [])
        data = signoff_report.build(self.dir, ["impl_1"])
        self.assertEqual(len(data["rows"]), len(signoff_report.CHECKLIST))

    def test_a_stage_that_did_not_run_is_not_checked_rather_than_passing(self):
        # The distinction this report exists to preserve.
        data = signoff_report.build(self.dir, ["impl_1"])
        statuses = set(row["status"] for row in data["rows"])
        self.assertEqual(statuses, {signoff_report.NOT_CHECKED})
        self.assertNotIn(signoff_report.PASS, statuses)

    def test_blocking_finding_marks_its_row_failed(self):
        self.write_stage("impl_1", [blocker("TIMING.HOLD_VIOLATION", "hold")])
        data = signoff_report.build(self.dir, ["impl_1"])
        timing = [r for r in data["rows"] if r["label"].startswith("4.")][0]
        self.assertEqual(timing["status"], signoff_report.FAIL)

    def test_waived_finding_marks_its_row_waived_not_failed(self):
        waived = blocker("TIMING.HOLD_VIOLATION", "hold")
        waived["waived"] = True
        waived["blocks_bringup"] = False
        waived["blocks_signoff"] = False
        waived["waiver"] = {"reason": "已評估", "approved_by": "me",
                            "date": "2026-08-09"}
        self.write_stage("impl_1", [waived])

        data = signoff_report.build(self.dir, ["impl_1"])
        timing = [r for r in data["rows"] if r["label"].startswith("4.")][0]
        self.assertEqual(timing["status"], signoff_report.WAIVED)

        text = signoff_report.render(data)
        self.assertIn("已核准的豁免項目", text)
        self.assertIn("已評估", text)

    def test_report_sections_and_signature_block(self):
        self.write_stage("impl_1", [blocker(), critical()])
        text = signoff_report.render(signoff_report.build(self.dir, ["impl_1"]))
        for heading in ("一、判定結論", "二、檢查項目清單", "三、環境與工具版本",
                        "四、輸入版本", "五、發現項目明細",
                        "六、已核准的豁免項目", "七、簽核"):
            self.assertIn(heading, text)
        self.assertIn("未檢查的項目", text)

    def test_report_names_the_audience(self):
        # It must be obvious this is not the file an agent should be reading.
        self.write_stage("impl_1", [])
        text = signoff_report.render(signoff_report.build(self.dir, ["impl_1"]))
        self.assertIn("AI agent 請改讀", text)

    def test_environment_and_inputs_are_included_when_recorded(self):
        self.write_stage("impl_1", [])
        self.write_json("manifests/environment_compare.json", {
            "environment": {
                "vivado": {"version": "2024.2", "build": "Build 5239630",
                           "install_root": "/tools/Vivado"},
                "os": {"pretty_name": "RHEL 8.8", "kernel": "4.18"},
                "python": {"version": "3.9.0", "executable": "/usr/bin/python3"},
                "host": {"hostname": "ws01"}},
            "comparison": {"changed": False}})
        self.write_json("manifests/compare_impl_1.json", {
            "manifest": {"git": {"commit": "abc123", "branch": "main",
                                 "dirty_inputs": ["constrs/main.xdc"]},
                         "digest": {"sources": "aaaa", "constraints": "bbbb"}}})

        text = signoff_report.render(signoff_report.build(self.dir, ["impl_1"]))
        self.assertIn("2024.2", text)
        self.assertIn("RHEL 8.8", text)
        self.assertIn("abc123", text)
        self.assertIn("無法由 git 重現", text)

    def test_digest_is_offered_for_pasting_into_a_waiver(self):
        item = blocker()
        item["digest"] = "abc123def456"
        self.write_stage("impl_1", [item])
        text = signoff_report.render(signoff_report.build(self.dir, ["impl_1"]))
        self.assertIn("abc123def456", text)

    def test_expired_waivers_are_called_out(self):
        path = os.path.join(self.dir, "risk_impl_1.json")
        data = assessment_with([blocker()])
        data["waivers"]["expired"] = [{"id": "X.BLOCK", "reason": "old",
                                       "recorded_digest": "aaa",
                                       "current_digest": "bbb"}]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)

        text = signoff_report.render(signoff_report.build(self.dir, ["impl_1"]))
        self.assertIn("已失效的豁免", text)

    def test_cli_writes_both_timestamped_and_latest(self):
        self.write_stage("impl_1", [])
        code = signoff_report.main(["--outdir", self.dir, "--stages", "impl_1"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(
            os.path.join(self.dir, "signoff_latest.md")))

    def test_cli_returns_nonzero_when_a_check_failed(self):
        self.write_stage("impl_1", [blocker("TIMING.HOLD_VIOLATION", "hold")])
        self.assertEqual(
            signoff_report.main(["--outdir", self.dir, "--stages", "impl_1"]), 1)


if __name__ == "__main__":
    unittest.main()
