# -*- coding: utf-8 -*-
"""Unit tests for Vivado log message analysis and its risk grading."""

import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import risk_rules        # noqa: E402
import vivado_log        # noqa: E402

SAMPLE_LOG = os.path.join(REPO, "examples", "sample_synth.log")


class TestMessageParsing(unittest.TestCase):
    def test_severity_id_and_body(self):
        parsed = vivado_log.parse_log_text(
            "WARNING: [Vivado 12-507] No objects matched 'get_ports clk'\n")
        message = parsed["messages"][0]
        self.assertEqual(message["severity"], "WARNING")
        self.assertEqual(message["id"], "Vivado 12-507")
        self.assertIn("No objects matched", message["examples"][0])

    def test_all_four_severities(self):
        parsed = vivado_log.parse_log_text("\n".join([
            "INFO: [Common 17-1] fine",
            "WARNING: [Synth 8-1] careful",
            "CRITICAL WARNING: [Timing 38-282] failed to meet timing",
            "ERROR: [Common 17-69] Command failed",
        ]))
        self.assertEqual(parsed["counts"],
                         {"INFO": 1, "WARNING": 1,
                          "CRITICAL WARNING": 1, "ERROR": 1})

    def test_non_message_lines_are_ignored(self):
        parsed = vivado_log.parse_log_text(
            "just some output\n#---\nStart of session\n")
        self.assertEqual(parsed["messages"], [])

    def test_repeated_messages_aggregate_rather_than_accumulate(self):
        # A synthesis log repeats a message per instance; keeping every line
        # would defeat the point of summarising at all.
        text = "\n".join(
            ["WARNING: [Synth 8-327] inferring latch for variable 'r{0}'".format(i)
             for i in range(50)])
        parsed = vivado_log.parse_log_text(text)
        self.assertEqual(len(parsed["messages"]), 1)
        self.assertEqual(parsed["messages"][0]["count"], 50)
        self.assertLessEqual(len(parsed["messages"][0]["examples"]),
                             vivado_log.MAX_EXAMPLES)

    def test_worst_severity_wins_for_one_id(self):
        parsed = vivado_log.parse_log_text("\n".join([
            "WARNING: [Synth 8-1] mild",
            "CRITICAL WARNING: [Synth 8-1] severe",
        ]))
        self.assertEqual(parsed["messages"][0]["severity"], "CRITICAL WARNING")

    def test_messages_sorted_worst_first(self):
        parsed = vivado_log.parse_log_text("\n".join([
            "INFO: [Common 17-1] fine",
            "ERROR: [Common 17-69] bad",
            "WARNING: [Synth 8-1] careful",
        ]))
        self.assertEqual([m["severity"] for m in parsed["messages"]],
                         ["ERROR", "WARNING", "INFO"])


class TestCuratedClassification(unittest.TestCase):
    """Severity alone would miss these; text matching is the durable half."""

    def test_constraint_not_applied_recognised_by_text(self):
        # A message ID we do not have on file, matched purely on wording.
        self.assertEqual(
            vivado_log.classify("Vivado 99-9999",
                                "No objects matched 'get_ports foo'"),
            "CONSTRAINT_NOT_APPLIED")

    def test_recognised_by_id_even_with_unfamiliar_wording(self):
        self.assertEqual(
            vivado_log.classify("Vivado 12-507", "completely different text"),
            "CONSTRAINT_NOT_APPLIED")

    def test_each_curated_class_has_a_working_pattern(self):
        cases = {
            "CONSTRAINT_NOT_APPLIED": "No objects matched 'get_cells x'",
            "UNBOUND_MODULE": "module 'fifo' is a black box",
            "INFERRED_LATCH": "inferring latch for variable 'state_reg'",
            "UNDRIVEN_NET": "Net foo does not have driver",
            "WIDTH_MISMATCH": "value was truncated to fit",
            "PLACEMENT_QUALITY": "Poor placement for routing",
        }
        for expected, text in cases.items():
            self.assertEqual(vivado_log.classify("Zzz 0-0", text), expected,
                             "failed to classify: {0}".format(text))

    def test_ordinary_message_is_not_classified(self):
        self.assertIsNone(
            vivado_log.classify("Synth 8-6157", "synthesizing module 'top'"))

    def test_grouping_by_class_merges_ids(self):
        parsed = vivado_log.parse_log_text("\n".join([
            "WARNING: [Vivado 12-507] No objects matched 'get_ports a'",
            "WARNING: [Common 17-55] 'get_ports' expects at least one object",
        ]))
        grouped = vivado_log.by_risk_class(parsed)
        self.assertEqual(grouped["CONSTRAINT_NOT_APPLIED"]["count"], 2)
        self.assertEqual(len(grouped["CONSTRAINT_NOT_APPLIED"]["ids"]), 2)


class TestLogFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_sample_log(self):
        parsed = vivado_log.parse_log(SAMPLE_LOG)
        self.assertTrue(parsed["available"])
        grouped = vivado_log.by_risk_class(parsed)
        self.assertIn("CONSTRAINT_NOT_APPLIED", grouped)
        self.assertIn("INFERRED_LATCH", grouped)
        self.assertEqual(grouped["INFERRED_LATCH"]["count"], 2)

    def test_missing_log_is_unavailable_not_an_exception(self):
        parsed = vivado_log.parse_log(os.path.join(self.dir, "absent.log"))
        self.assertFalse(parsed["available"])
        self.assertIn("reason", parsed)

    def test_truncated_log_does_not_raise(self):
        with open(SAMPLE_LOG, encoding="utf-8") as handle:
            full = handle.read()
        for cut in (5, 60, 300):
            path = os.path.join(self.dir, "cut.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(full[:cut])
            self.assertIn("available", vivado_log.parse_log(path))

    def test_multiple_logs_merge(self):
        first = os.path.join(self.dir, "a.log")
        second = os.path.join(self.dir, "b.log")
        with open(first, "w", encoding="utf-8") as handle:
            handle.write("WARNING: [Synth 8-327] inferring latch for 'a'\n")
        with open(second, "w", encoding="utf-8") as handle:
            handle.write("WARNING: [Synth 8-327] inferring latch for 'b'\n")

        merged = vivado_log.parse_logs([first, second])
        self.assertTrue(merged["available"])
        self.assertEqual(len(merged["messages"]), 1)
        self.assertEqual(merged["messages"][0]["count"], 2)
        self.assertEqual(len(merged["sources"]), 2)

    def test_unreadable_logs_are_listed_not_silently_dropped(self):
        merged = vivado_log.parse_logs([os.path.join(self.dir, "gone.log")])
        self.assertFalse(merged["available"])
        self.assertEqual(len(merged["unavailable"]), 1)


class TestLogRiskGrading(unittest.TestCase):
    def _grade(self, text):
        logs = dict(vivado_log.parse_log_text(text), available=True)
        return risk_rules.evaluate(logs=logs)

    def test_warning_level_constraint_failure_is_a_blocker(self):
        # The whole reason the curated table exists: Vivado calls this a plain
        # WARNING, but it means the timing numbers are computed against
        # constraints that were never applied.
        assessment = self._grade(
            "WARNING: [Vivado 12-507] No objects matched 'get_ports clk_in'\n")
        found = [f for f in assessment["findings"]
                 if f["id"] == "LOG.CONSTRAINT_NOT_APPLIED"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], risk_rules.BLOCKER)
        self.assertFalse(assessment["verdict"]["bringup_ok"])

    def test_unbound_module_is_a_blocker(self):
        assessment = self._grade(
            "WARNING: [Synth 8-448] module 'fifo' is a black box\n")
        found = [f for f in assessment["findings"]
                 if f["id"] == "LOG.UNBOUND_MODULE"][0]
        self.assertEqual(found["severity"], risk_rules.BLOCKER)

    def test_inferred_latch_blocks_signoff_only(self):
        assessment = self._grade(
            "WARNING: [Synth 8-327] inferring latch for variable 'x'\n")
        found = [f for f in assessment["findings"]
                 if f["id"] == "LOG.INFERRED_LATCH"][0]
        self.assertEqual(found["severity"], risk_rules.CRITICAL)
        self.assertTrue(assessment["verdict"]["bringup_ok"])

    def test_width_mismatch_is_only_a_warning(self):
        assessment = self._grade(
            "WARNING: [Synth 8-6014] value was truncated to fit\n")
        found = [f for f in assessment["findings"]
                 if f["id"] == "LOG.WIDTH_MISMATCH"][0]
        self.assertEqual(found["severity"], risk_rules.WARNING)

    def test_vivado_error_is_always_a_blocker(self):
        assessment = self._grade(
            "ERROR: [Common 17-69] Command failed: something broke\n")
        found = [f for f in assessment["findings"] if f["id"] == "LOG.ERROR"][0]
        self.assertEqual(found["severity"], risk_rules.BLOCKER)

    def test_ordinary_log_produces_no_findings(self):
        assessment = self._grade("\n".join([
            "INFO: [Synth 8-6157] synthesizing module 'top'",
            "INFO: [Common 17-83] Releasing license",
        ]))
        self.assertEqual([f for f in assessment["findings"]
                          if f["id"].startswith("LOG.")], [])


if __name__ == "__main__":
    unittest.main()
