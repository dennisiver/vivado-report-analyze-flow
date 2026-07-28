# -*- coding: utf-8 -*-
"""Unit tests for the Vivado timing report parser and the summary renderer."""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import analyze_run              # noqa: E402
import trend                    # noqa: E402
import vivado_report_parser as vrp  # noqa: E402

SAMPLE = os.path.join(REPO, "examples", "sample_timing_summary.rpt")


class TestParseSummary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = vrp.parse_timing_summary_file(SAMPLE)

    def test_metadata(self):
        meta = self.parsed["meta"]
        self.assertEqual(meta["design"], "top")
        self.assertEqual(meta["device"], "7k325t-ffg900")
        self.assertEqual(meta["design_state"], "Routed")
        self.assertIn("2021.2", meta["tool_version"])

    def test_design_timing_summary(self):
        summary = self.parsed["summary"]
        self.assertAlmostEqual(summary["wns"], -0.234)
        self.assertAlmostEqual(summary["tns"], -12.456)
        self.assertEqual(summary["tns_failing_endpoints"], 45)
        self.assertEqual(summary["tns_total_endpoints"], 12034)
        self.assertAlmostEqual(summary["whs"], 0.021)
        self.assertEqual(summary["ths_failing_endpoints"], 0)
        self.assertAlmostEqual(summary["wpws"], 3.5)

    def test_constraints_met_flag(self):
        self.assertIs(self.parsed["constraints_met"], False)

    def test_check_timing_counts(self):
        check = self.parsed["check_timing"]
        self.assertEqual(check["total"], 1222)
        self.assertEqual(check["items"]["unconstrained_internal_endpoints"], 1204)
        self.assertEqual(check["items"]["no_input_delay"], 14)
        self.assertEqual(check["items"]["no_clock"], 0)

    def test_clocks(self):
        clocks = dict((c["name"], c) for c in self.parsed["clocks"])
        self.assertEqual(sorted(clocks), ["clk_100", "clk_200"])
        self.assertAlmostEqual(clocks["clk_200"]["period_ns"], 5.0)
        self.assertAlmostEqual(clocks["clk_100"]["frequency_mhz"], 100.0)

    def test_paths_parsed(self):
        paths = self.parsed["paths"]
        self.assertEqual(len(paths), 5)

        worst = vrp.worst_paths(paths, limit=10, delay_kind="max")[0]
        self.assertAlmostEqual(worst["slack_ns"], -0.234)
        self.assertEqual(worst["status"], "VIOLATED")
        self.assertEqual(worst["source"], "u_core/u_pipe/data_reg[3]/C")
        self.assertEqual(worst["destination"], "u_core/u_pipe/accum_reg[7]/D")
        self.assertEqual(worst["source_clock"], "clk_200")
        self.assertEqual(worst["destination_clock"], "clk_200")
        self.assertEqual(worst["path_group"], "clk_200")
        self.assertEqual(worst["logic_levels"], 12)
        self.assertEqual(worst["logic_levels_detail"], "LUT6=6 CARRY4=4 LUT3=2")
        self.assertAlmostEqual(worst["data_path_delay_ns"], 4.987)
        self.assertAlmostEqual(worst["route_pct"], 75.737)
        self.assertAlmostEqual(worst["requirement_ns"], 5.0)
        self.assertAlmostEqual(worst["clock_uncertainty_ns"], 0.035)

    def test_hold_paths_are_separated_from_setup(self):
        setup = vrp.worst_paths(self.parsed["paths"], limit=10, delay_kind="max")
        hold = vrp.worst_paths(self.parsed["paths"], limit=10, delay_kind="min")
        self.assertEqual(len(setup), 4)
        self.assertEqual(len(hold), 1)
        self.assertEqual(hold[0]["path_type"], "Hold (Min at Fast Process Corner)")

    def test_worst_paths_sorted_across_clock_groups(self):
        # The report lists clk_200 then clk_100; a global sort must interleave
        # them by slack rather than trust the report's per-group ordering.
        slacks = [p["slack_ns"]
                  for p in vrp.worst_paths(self.parsed["paths"], limit=10)]
        self.assertEqual(slacks, sorted(slacks))

    def test_extract_path_block_round_trip(self):
        worst = vrp.worst_paths(self.parsed["paths"], limit=1)[0]
        block = vrp.extract_path_block(
            SAMPLE, worst["line_start"], worst["line_end"])
        self.assertIn("Slack (VIOLATED)", block)
        self.assertIn("u_core/u_pipe/accum_reg[7]/D", block)
        # The drill-down must include the per-pin delay table, which is the
        # part the compact summary deliberately drops.
        self.assertIn("Netlist Resource(s)", block)


class TestParserRobustness(unittest.TestCase):
    def test_empty_report(self):
        parsed = vrp.parse_timing_summary("")
        self.assertEqual(parsed["paths"], [])
        self.assertEqual(parsed["summary"], {})
        self.assertIsNone(parsed["constraints_met"])

    def test_na_values_become_none(self):
        text = "\n".join([
            "    WNS(ns)      TNS(ns)  TNS Failing Endpoints  TNS Total Endpoints",
            "    -------      -------  ---------------------  -------------------",
            "         NA           NA                     NA                   NA",
        ])
        summary = vrp.parse_timing_summary(text)["summary"]
        self.assertIsNone(summary["wns"])
        self.assertIsNone(summary["tns_failing_endpoints"])

    def test_met_constraints_report(self):
        parsed = vrp.parse_timing_summary(
            "All user specified timing constraints are met.\n")
        self.assertIs(parsed["constraints_met"], True)


class TestTrend(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_history_round_trip_and_stage_filter(self):
        trend.append_history(self.dir, {"stage": "impl_1", "wns": -0.5})
        trend.append_history(self.dir, {"stage": "synth_1", "wns": 0.1})
        trend.append_history(self.dir, {"stage": "impl_1", "wns": -0.2})

        impl = trend.load_history(self.dir, stage="impl_1")
        self.assertEqual([r["wns"] for r in impl], [-0.5, -0.2])
        self.assertEqual(len(trend.load_history(self.dir)), 3)

    def test_corrupt_line_does_not_lose_history(self):
        trend.append_history(self.dir, {"stage": "impl_1", "wns": -0.5})
        with open(trend.history_path(self.dir), "a") as handle:
            handle.write("{ truncated\n")
        trend.append_history(self.dir, {"stage": "impl_1", "wns": -0.4})

        records = trend.load_history(self.dir, stage="impl_1")
        self.assertEqual([r["wns"] for r in records], [-0.5, -0.4])

    def test_delta_direction_for_slack_and_counts(self):
        # More slack is better; more failing endpoints is worse.
        self.assertEqual(trend.delta({"wns": -0.1}, {"wns": -0.5}, "wns")[1], "better")
        self.assertEqual(trend.delta({"wns": -0.5}, {"wns": -0.1}, "wns")[1], "worse")
        self.assertEqual(
            trend.delta({"tns_failing_endpoints": 10},
                        {"tns_failing_endpoints": 4},
                        "tns_failing_endpoints")[1], "worse")
        self.assertEqual(trend.delta({"wns": 1.0}, {"wns": 1.0}, "wns")[1], "same")
        self.assertEqual(trend.delta({"wns": 1.0}, None, "wns"), (None, None))

    def test_endpoint_diff(self):
        paths = [
            {"destination": "a/reg/D", "slack_ns": -0.2},
            {"destination": "b/reg/D", "slack_ns": -0.1},
            {"destination": "c/reg/D", "slack_ns": 0.4},  # met, not a violation
        ]
        diff = trend.endpoint_diff(paths, ["b/reg/D", "z/reg/D"])
        self.assertEqual(diff["new"], ["a/reg/D"])
        self.assertEqual(diff["resolved"], ["z/reg/D"])
        self.assertNotIn("c/reg/D", diff["current"])


class TestAnalyzeRunCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _analyze(self):
        return analyze_run.main([
            "--stage", "impl_1",
            "--timing-summary", SAMPLE,
            "--outdir", self.dir,
        ])

    def test_writes_summary_and_history(self):
        self.assertEqual(self._analyze(), 0)

        latest = os.path.join(self.dir, "latest_impl_1.md")
        self.assertTrue(os.path.isfile(latest))
        with open(latest, encoding="utf-8") as handle:
            markdown = handle.read()

        self.assertIn("-0.234", markdown)
        self.assertIn("Timing constraints are NOT met", markdown)
        self.assertIn("unconstrained_internal_endpoints", markdown)
        self.assertIn("u_core/u_pipe/accum_reg[7]/D", markdown)

        records = trend.load_history(self.dir, stage="impl_1")
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["wns"], -0.234)

    def test_summary_stays_small_enough_to_read(self):
        # The whole point of the flow: the agent reads this instead of the
        # raw report, so it has to stay far smaller than the source.
        self._analyze()
        with open(os.path.join(self.dir, "latest_impl_1.md"),
                  encoding="utf-8") as handle:
            summary_lines = len(handle.read().splitlines())
        with open(SAMPLE, encoding="utf-8") as handle:
            report_lines = len(handle.read().splitlines())

        self.assertLess(summary_lines, 90)
        self.assertLess(summary_lines, report_lines)

    def test_second_run_records_trend(self):
        self._analyze()
        self._analyze()
        with open(os.path.join(self.dir, "latest_impl_1.md"),
                  encoding="utf-8") as handle:
            markdown = handle.read()
        self.assertIn("Trend", markdown)
        self.assertIn("no change", markdown)
        self.assertEqual(len(trend.load_history(self.dir, stage="impl_1")), 2)

    def test_show_path_drills_into_one_path_only(self):
        self._analyze()
        pointer = json.load(
            open(os.path.join(self.dir, "latest_impl_1.json"), encoding="utf-8"))
        run_json = pointer["run_json"]

        code = analyze_run.main([
            "--outdir", self.dir, "--stage", "impl_1",
            "--show-path", "accum_reg[7]", "--run-json", run_json,
        ])
        self.assertEqual(code, 0)

    def test_show_path_reports_unknown_endpoint(self):
        self._analyze()
        code = analyze_run.main([
            "--outdir", self.dir, "--stage", "impl_1",
            "--show-path", "no_such_endpoint_anywhere",
        ])
        self.assertEqual(code, 1)

    def test_missing_report_is_an_error_not_a_crash(self):
        code = analyze_run.main([
            "--stage", "impl_1",
            "--timing-summary", os.path.join(self.dir, "nope.rpt"),
            "--outdir", self.dir,
        ])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
