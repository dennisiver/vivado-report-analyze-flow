# -*- coding: utf-8 -*-
"""Unit tests for the risk grading engine.

The property under test throughout is that severity always agrees with the two
verdict flags, and that a rule blocks bring-up only when a bench session would
genuinely be untrustworthy -- not merely when something is wrong.
"""

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import risk_report                  # noqa: E402
import risk_rules                   # noqa: E402
from risk_rules import BLOCKER, CRITICAL, INFO, WARNING  # noqa: E402


def timing(summary=None, checks=None, paths=None, clocks=None):
    """Minimal parsed-timing structure for driving the rules."""
    return {
        "summary": summary or {},
        "check_timing": {"items": checks or {}},
        "paths": paths or [],
        "clocks": clocks or [{"name": "clk", "period_ns": 10.0}],
        "meta": {"design": "top"},
    }


def available(payload):
    result = dict(payload)
    result["available"] = True
    return result


def ids(assessment, identifier):
    return [f for f in assessment["findings"] if f["id"] == identifier]


def one(assessment, identifier):
    matches = ids(assessment, identifier)
    assert len(matches) == 1, "expected exactly one {0}, got {1}".format(
        identifier, len(matches))
    return matches[0]


class TestSeverityDerivation(unittest.TestCase):
    def test_severity_follows_the_two_flags(self):
        self.assertEqual(risk_rules.severity_of(True, True), BLOCKER)
        self.assertEqual(risk_rules.severity_of(True, False), BLOCKER)
        self.assertEqual(risk_rules.severity_of(False, True), CRITICAL)
        self.assertEqual(risk_rules.severity_of(False, False), WARNING)
        self.assertEqual(risk_rules.severity_of(False, False, notable=False), INFO)

    def test_every_finding_is_internally_consistent(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": -0.2, "whs": -0.05, "wpws": -0.1},
                          checks={"no_clock": 3, "no_input_delay": 2}))
        for item in assessment["findings"]:
            self.assertEqual(
                item["severity"],
                risk_rules.severity_of(item["blocks_bringup"],
                                       item["blocks_signoff"],
                                       item["severity"] != INFO),
                "inconsistent grading on {0}".format(item["id"]))


class TestTimingRules(unittest.TestCase):
    def test_hold_violation_blocks_bringup_and_signoff(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"whs": -0.045, "ths_failing_endpoints": 12}))
        found = one(assessment, "TIMING.HOLD_VIOLATION")
        self.assertEqual(found["severity"], BLOCKER)
        self.assertTrue(found["blocks_bringup"])
        self.assertTrue(found["blocks_signoff"])
        self.assertFalse(assessment["verdict"]["bringup_ok"])

    def test_modest_setup_violation_allows_bringup(self):
        # -0.234 ns against a 10 ns period is 2.3%: reduce the clock and the
        # bench session is still meaningful.
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": -0.234, "tns_failing_endpoints": 45}))
        found = one(assessment, "TIMING.SETUP_VIOLATION")
        self.assertEqual(found["severity"], CRITICAL)
        self.assertFalse(found["blocks_bringup"])
        self.assertTrue(found["blocks_signoff"])
        self.assertTrue(assessment["verdict"]["bringup_ok"])
        self.assertFalse(assessment["verdict"]["signoff_ok"])

    def test_severe_setup_violation_escalates_to_blocker(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": -2.5}))
        found = one(assessment, "TIMING.SETUP_SEVERE")
        self.assertEqual(found["severity"], BLOCKER)
        self.assertEqual(ids(assessment, "TIMING.SETUP_VIOLATION"), [])

    def test_setup_severity_scales_with_the_clock_period(self):
        # The same slack is severe on a fast clock and tolerable on a slow one.
        fast = risk_rules.evaluate(
            timing=timing(summary={"wns": -0.6}, clocks=[{"period_ns": 5.0}]))
        slow = risk_rules.evaluate(
            timing=timing(summary={"wns": -0.6}, clocks=[{"period_ns": 20.0}]))
        self.assertEqual(len(ids(fast, "TIMING.SETUP_SEVERE")), 1)
        self.assertEqual(len(ids(slow, "TIMING.SETUP_VIOLATION")), 1)

    def test_period_taken_from_worst_path_requirement(self):
        assessment = risk_rules.evaluate(timing=timing(
            summary={"wns": -0.6},
            paths=[{"delay_kind": "max", "slack_ns": -0.6,
                    "requirement_ns": 5.0}],
            clocks=[{"period_ns": 20.0}]))
        self.assertEqual(len(ids(assessment, "TIMING.SETUP_SEVERE")), 1)

    def test_no_clock_blocks_everything(self):
        assessment = risk_rules.evaluate(timing=timing(checks={"no_clock": 7}))
        found = one(assessment, "TIMING.NO_CLOCK")
        self.assertEqual(found["severity"], BLOCKER)
        self.assertFalse(assessment["verdict"]["bringup_ok"])

    def test_combinational_loop_blocks_everything(self):
        assessment = risk_rules.evaluate(timing=timing(checks={"loops": 1}))
        self.assertEqual(one(assessment, "TIMING.COMB_LOOP")["severity"], BLOCKER)

    def test_unconstrained_endpoints_escalate_with_share(self):
        small = risk_rules.evaluate(timing=timing(
            summary={"tns_total_endpoints": 100000},
            checks={"unconstrained_internal_endpoints": 5}))
        self.assertEqual(
            one(small, "TIMING.UNCONSTRAINED_ENDPOINTS")["severity"], CRITICAL)

        large = risk_rules.evaluate(timing=timing(
            summary={"tns_total_endpoints": 12034},
            checks={"unconstrained_internal_endpoints": 1204}))
        self.assertEqual(
            one(large, "TIMING.UNCONSTRAINED_SEVERE")["severity"], BLOCKER)

    def test_missing_io_delay_blocks_signoff_only(self):
        assessment = risk_rules.evaluate(timing=timing(
            checks={"no_input_delay": 14, "no_output_delay": 4}))
        found = one(assessment, "TIMING.NO_IO_DELAY")
        self.assertEqual(found["severity"], CRITICAL)
        self.assertEqual(found["evidence"]["no_input_delay"], 14)
        # The wording must tell the user when to treat it as a blocker.
        self.assertIn("BLOCKER", found["risk"])

    def test_route_and_logic_hints_are_warnings_only(self):
        assessment = risk_rules.evaluate(timing=timing(
            summary={"wns": 0.5},
            paths=[{"delay_kind": "max", "slack_ns": 0.5, "route_pct": 85.0,
                    "logic_levels": 22, "destination": "a/reg/D"}]))
        self.assertEqual(
            one(assessment, "TIMING.ROUTE_DOMINATED")["severity"], WARNING)
        self.assertEqual(
            one(assessment, "TIMING.DEEP_LOGIC")["severity"], WARNING)
        self.assertTrue(assessment["verdict"]["bringup_ok"])
        self.assertTrue(assessment["verdict"]["signoff_ok"])

    def test_clean_timing_produces_no_timing_findings(self):
        assessment = risk_rules.evaluate(timing=timing(
            summary={"wns": 0.5, "whs": 0.02, "wpws": 3.5},
            checks={"no_clock": 0, "unconstrained_internal_endpoints": 0}))
        self.assertEqual(
            [f for f in assessment["findings"] if f["id"].startswith("TIMING.")],
            [])


class TestCdcAndClockRules(unittest.TestCase):
    def test_critical_cdc_blocks_bringup(self):
        assessment = risk_rules.evaluate(reports={"cdc": available({
            "findings": [
                {"severity": "Critical", "id": "CDC-1",
                 "description": "1-bit unknown CDC circuitry",
                 "source_clock": "clk_100", "destination_clock": "clk_200"},
                {"severity": "Critical", "id": "CDC-1",
                 "description": "1-bit unknown CDC circuitry",
                 "source_clock": "clk_200", "destination_clock": "clk_100"},
            ]})})
        found = one(assessment, "CDC.CRITICAL")
        self.assertEqual(found["severity"], BLOCKER)
        self.assertEqual(found["evidence"]["count"], 2)
        self.assertIn("metastability", found["risk"])

    def test_warning_cdc_blocks_signoff_only(self):
        assessment = risk_rules.evaluate(reports={"cdc": available({
            "findings": [{"severity": "Warning", "id": "CDC-4",
                          "description": "Multi-bit unknown CDC circuitry"}]})})
        self.assertEqual(one(assessment, "CDC.WARNING")["severity"], CRITICAL)

    def test_info_cdc_is_not_reported_as_a_risk(self):
        assessment = risk_rules.evaluate(reports={"cdc": available({
            "findings": [{"severity": "Info", "id": "CDC-11",
                          "description": "synchronized with ASYNC_REG"}]})})
        self.assertEqual(ids(assessment, "CDC.CRITICAL"), [])
        self.assertEqual(ids(assessment, "CDC.WARNING"), [])

    def test_unsafe_clock_pair_blocks_bringup(self):
        assessment = risk_rules.evaluate(reports={"clock_interaction": available({
            "pairs": [
                {"source_clock": "a", "destination_clock": "b",
                 "classification": "Timed (Unsafe)"},
                {"source_clock": "c", "destination_clock": "d",
                 "classification": "No Common Clock"},
            ]})})
        found = one(assessment, "CLK.NO_COMMON_CLOCK")
        self.assertEqual(found["severity"], BLOCKER)
        self.assertEqual(found["evidence"]["count"], 2)

    def test_safely_timed_pairs_produce_nothing(self):
        # "Safely Timed" contains the substring "safe"; the rule must not
        # match on it.
        assessment = risk_rules.evaluate(reports={"clock_interaction": available({
            "pairs": [{"source_clock": "a", "destination_clock": "a",
                       "classification": "Safely Timed"}]})})
        self.assertEqual(ids(assessment, "CLK.NO_COMMON_CLOCK"), [])
        self.assertEqual(ids(assessment, "CLK.PARTIAL_FALSE_PATH"), [])

    def test_partial_false_path_blocks_signoff_only(self):
        assessment = risk_rules.evaluate(reports={"clock_interaction": available({
            "pairs": [{"source_clock": "a", "destination_clock": "b",
                       "classification": "Partial False Path"}]})})
        self.assertEqual(
            one(assessment, "CLK.PARTIAL_FALSE_PATH")["severity"], CRITICAL)


class TestRuleReportGrading(unittest.TestCase):
    def _drc(self, rule, severity):
        return risk_rules.evaluate(reports={"drc": available({
            "violations": [{"rule": rule, "severity": severity,
                            "title": "t", "detail": "d"}]})})

    def test_bitstream_blocking_drc_rules(self):
        for rule in ("NSTD-1", "UCIO-1"):
            found = one(self._drc(rule, "Critical Warning"),
                        "DRC.BITSTREAM_BLOCKING")
            self.assertEqual(found["severity"], BLOCKER)
            self.assertIn("write_bitstream", found["risk"])

    def test_drc_severity_mapping(self):
        self.assertEqual(one(self._drc("XYZ-1", "Error"),
                             "DRC.ERROR")["severity"], BLOCKER)
        self.assertEqual(one(self._drc("XYZ-1", "Critical Warning"),
                             "DRC.CRITICAL_WARNING")["severity"], CRITICAL)
        self.assertEqual(one(self._drc("XYZ-1", "Warning"),
                             "DRC.WARNING")["severity"], WARNING)
        self.assertEqual(one(self._drc("XYZ-1", "Advisory"),
                             "DRC.ADVISORY")["severity"], INFO)

    def _methodology(self, rule, severity="Critical Warning"):
        return risk_rules.evaluate(reports={"methodology": available({
            "violations": [{"rule": rule, "severity": severity,
                            "title": "t", "detail": "d"}]})})

    def test_unsafe_timing_methodology_rules_block_bringup(self):
        for rule in ("TIMING-6", "TIMING-7", "TIMING-9"):
            found = one(self._methodology(rule), "METH.TIMING_UNSAFE")
            self.assertEqual(found["severity"], BLOCKER)

    def test_clock_path_methodology_rules_block_signoff(self):
        for rule in ("TIMING-14", "TIMING-15"):
            self.assertEqual(
                one(self._methodology(rule), "METH.CLOCK_PATH")["severity"],
                CRITICAL)

    def test_other_methodology_rules(self):
        self.assertEqual(one(self._methodology("SYNTH-6", "Warning"),
                             "METH.WARNING")["severity"], WARNING)

    def test_violations_of_the_same_rule_are_grouped(self):
        assessment = risk_rules.evaluate(reports={"drc": available({
            "violations": [{"rule": "NSTD-1", "severity": "Critical Warning",
                            "title": "t", "detail": "d"}] * 5})})
        found = one(assessment, "DRC.BITSTREAM_BLOCKING")
        self.assertEqual(found["evidence"]["count"], 5)


class TestUtilizationRules(unittest.TestCase):
    def _util(self, percent):
        return risk_rules.evaluate(reports={"utilization": available({
            "resources": {"lut": {"name": "Slice LUTs", "used": 100,
                                  "available": 200, "util_percent": percent}}})})

    def test_thresholds(self):
        self.assertEqual(one(self._util(95.0), "UTIL.CRITICAL")["severity"],
                         CRITICAL)
        self.assertEqual(one(self._util(85.0), "UTIL.HIGH")["severity"], WARNING)
        self.assertEqual(ids(self._util(40.0), "UTIL.HIGH"), [])

    def test_high_utilization_never_blocks_bringup(self):
        # A congested design is unreliable to iterate on, but it does not make
        # a bench session meaningless.
        self.assertTrue(self._util(99.0)["verdict"]["bringup_ok"])

    def test_control_sets_need_register_count_for_a_ratio(self):
        without = risk_rules.evaluate(reports={
            "control_sets": available({"unique_control_sets": 34210})})
        self.assertEqual(ids(without, "CTRLSET.HIGH"), [])

        with_registers = risk_rules.evaluate(reports={
            "control_sets": available({"unique_control_sets": 34210}),
            "utilization": available({"resources": {
                "register": {"name": "Slice Registers", "used": 132470,
                             "available": 407600, "util_percent": 32.5}}})})
        self.assertEqual(
            one(with_registers, "CTRLSET.HIGH")["severity"], WARNING)


class TestProvenanceRules(unittest.TestCase):
    def test_uncommitted_design_files_block_signoff(self):
        assessment = risk_rules.evaluate(manifest={
            "git": {"available": True, "dirty_inputs": ["constrs/main.xdc"]}})
        found = one(assessment, "PROV.DIRTY_INPUTS")
        self.assertEqual(found["severity"], CRITICAL)
        self.assertTrue(assessment["verdict"]["bringup_ok"])
        self.assertFalse(assessment["verdict"]["signoff_ok"])

    def test_clean_tree_produces_nothing(self):
        assessment = risk_rules.evaluate(manifest={
            "git": {"available": True, "dirty_inputs": []}})
        self.assertEqual(ids(assessment, "PROV.DIRTY_INPUTS"), [])

    def test_preflight_warnings_surface_as_warnings(self):
        assessment = risk_rules.evaluate(
            preflight={"warnings": ["constraint not used in synthesis"]})
        self.assertEqual(
            one(assessment, "PROV.PREFLIGHT_WARNINGS")["severity"], WARNING)


class TestCoverageGaps(unittest.TestCase):
    """A report that did not run must never read as a clean result."""

    def test_unavailable_safety_report_is_flagged_and_blocks_signoff(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": 0.5, "whs": 0.02}),
            reports={"cdc": {"available": False, "reason": "report not generated"}},
            requested_reports={"cdc"})

        gaps = ids(assessment, "META.REPORT_UNAVAILABLE")
        self.assertEqual(len(gaps), 1)
        self.assertTrue(gaps[0]["blocks_signoff"])
        # The design is otherwise clean, but sign-off must not be granted while
        # clock domain crossings were never checked.
        self.assertFalse(assessment["verdict"]["signoff_ok"])
        self.assertIn("沒有涵蓋", gaps[0]["risk"])

    def test_gap_is_visible_in_the_rendered_summary(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": 0.5, "whs": 0.02}),
            reports={"cdc": {"available": False, "reason": "boom"}},
            requested_reports={"cdc"})
        block = risk_report.render_verdict_block(assessment, "risk.md")
        self.assertIn("未檢查的項目", block)
        self.assertIn("CDC", block)

    def test_optional_report_gap_does_not_block_signoff(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": 0.5, "whs": 0.02}),
            reports={"control_sets": {"available": False, "reason": "n/a"}},
            requested_reports={"control_sets"})
        self.assertTrue(assessment["verdict"]["signoff_ok"])
        self.assertEqual(
            one(assessment, "META.REPORT_UNAVAILABLE")["severity"], INFO)

    def test_reports_not_requested_are_not_reported_as_gaps(self):
        assessment = risk_rules.evaluate(
            reports={"cdc": {"available": False, "reason": "not run"}},
            requested_reports=set())
        self.assertEqual(ids(assessment, "META.REPORT_UNAVAILABLE"), [])


class TestOverallVerdict(unittest.TestCase):
    def test_fully_clean_design_passes_both_verdicts(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": 0.5, "whs": 0.02, "wpws": 3.5,
                                   "tns_total_endpoints": 1000},
                          checks={"no_clock": 0, "no_input_delay": 0}),
            reports={
                "cdc": available({"findings": []}),
                "drc": available({"violations": []}),
                "methodology": available({"violations": []}),
                "utilization": available({"resources": {
                    "lut": {"name": "LUT", "used": 10, "available": 100,
                            "util_percent": 10.0}}}),
                "clock_interaction": available({"pairs": []}),
                "control_sets": available({"unique_control_sets": 10}),
            },
            manifest={"git": {"available": True, "dirty_inputs": []}},
            requested_reports=set(risk_rules.SAFETY_RELEVANT_REPORTS))

        verdict = assessment["verdict"]
        self.assertTrue(verdict["bringup_ok"])
        self.assertTrue(verdict["signoff_ok"])
        self.assertEqual(verdict["blockers"], 0)
        self.assertEqual(assessment["findings"], [])

    def test_findings_are_ordered_worst_first(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": -0.2, "whs": -0.05},
                          checks={"no_input_delay": 3}))
        severities = [f["severity"] for f in assessment["findings"]]
        ranks = [risk_rules.SEVERITY_ORDER.index(s) for s in severities]
        self.assertEqual(ranks, sorted(ranks))

    def test_thresholds_can_be_overridden(self):
        strict = risk_rules.evaluate(
            timing=timing(summary={"wns": -0.234}),
            thresholds={"setup_severe_fraction": 0.01})
        self.assertEqual(len(ids(strict, "TIMING.SETUP_SEVERE")), 1)

    def test_evaluate_tolerates_completely_empty_input(self):
        assessment = risk_rules.evaluate()
        self.assertTrue(assessment["verdict"]["bringup_ok"])
        self.assertEqual(assessment["findings"], [])


class TestRiskReportRendering(unittest.TestCase):
    def _assessment(self):
        return risk_rules.evaluate(
            timing=timing(summary={"wns": -0.234, "whs": -0.045,
                                   "tns_failing_endpoints": 45},
                          checks={"no_input_delay": 14}))

    def test_verdict_block_lists_blockers_with_risk_and_action(self):
        block = risk_report.render_verdict_block(self._assessment(), "risk.md")
        self.assertIn("驗證風險判定", block)
        self.assertIn("必須先解決（BLOCKER）", block)
        self.assertIn("風險：", block)
        self.assertIn("建議：", block)
        self.assertIn("Hold 違規", block)

    def test_verdict_block_stays_compact(self):
        # It sits at the top of latest_<run>.md, so it must not crowd out the
        # timing content the agent also needs.
        block = risk_report.render_verdict_block(self._assessment(), "risk.md")
        self.assertLess(len(block.splitlines()), 45)

    def test_clean_design_says_so_explicitly(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": 0.5, "whs": 0.02}))
        block = risk_report.render_verdict_block(assessment, "risk.md")
        self.assertIn("✅", block)
        self.assertIn("沒有阻擋上板驗證的項目", block)

    def test_full_report_covers_every_severity_present(self):
        report = risk_report.render_full_report(
            self._assessment(), "impl_1", "2026-07-28T00:00:00", design="top")
        self.assertIn("FPGA 驗證風險報告", report)
        self.assertIn("BLOCKER", report)
        self.assertIn("CRITICAL", report)
        self.assertIn("阻擋上板：是", report)
        self.assertIn("阻擋上板：否", report)

    def test_full_report_on_a_clean_design(self):
        assessment = risk_rules.evaluate(
            timing=timing(summary={"wns": 0.5, "whs": 0.02}))
        report = risk_report.render_full_report(
            assessment, "impl_1", "2026-07-28T00:00:00")
        self.assertIn("可以", report)


if __name__ == "__main__":
    unittest.main()
