# -*- coding: utf-8 -*-
"""Unit tests for the generic table parsers and the per-report parsers."""

import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import report_tables as tables      # noqa: E402
import vivado_reports as reports    # noqa: E402

EXAMPLES = os.path.join(REPO, "examples")


def sample(kind):
    return os.path.join(EXAMPLES, "sample_{0}.rpt".format(kind))


class TestTableParsers(unittest.TestCase):
    def test_pipe_table(self):
        text = "\n".join([
            "1. Slice Logic",
            "+------------+------+-------+",
            "| Site Type  | Used | Util% |",
            "+------------+------+-------+",
            "| Slice LUTs | 1234 | 90.00 |",
            "+------------+------+-------+",
        ])
        parsed = tables.parse_pipe_tables(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], "1. Slice Logic")
        self.assertEqual(parsed[0]["rows"][0]["site_type"], "Slice LUTs")
        self.assertEqual(parsed[0]["rows"][0]["used"], "1234")

    def test_ruler_table_last_column_takes_rest_of_line(self):
        text = "\n".join([
            "Severity  ID       Description",
            "--------  -------  -----------",
            "Critical  CDC-1    1-bit unknown CDC circuitry across domains",
        ])
        parsed = tables.parse_ruler_tables(text)
        self.assertEqual(len(parsed), 1)
        row = parsed[0]["rows"][0]
        self.assertEqual(row["severity"], "Critical")
        self.assertEqual(row["id"], "CDC-1")
        self.assertEqual(row["description"],
                         "1-bit unknown CDC circuitry across domains")

    def test_find_table_by_columns_and_title(self):
        text = "\n".join([
            "First",
            "+---+---+",
            "| A | B |",
            "+---+---+",
            "| 1 | 2 |",
            "+---+---+",
            "",
            "Second",
            "+---+---+",
            "| A | B |",
            "+---+---+",
            "| 3 | 4 |",
            "+---+---+",
        ])
        parsed = tables.parse_pipe_tables(text)
        self.assertIsNone(tables.find_table(parsed, ["A", "Z"]))
        chosen = tables.find_table(parsed, ["A", "B"], title_hint="second")
        self.assertEqual(chosen["rows"][0]["a"], "3")

    def test_violation_blocks(self):
        text = "\n".join([
            "Violations found: 2",
            "",
            "NSTD-1#1 Critical Warning",
            "Unspecified I/O Standard",
            "12 out of 186 logical ports use I/O standard value 'DEFAULT'.",
            "Related violations: <none>",
            "",
            "TIMING-6#3 Warning",
            "No common primary clock",
            "The clocks are related but have no common primary clock.",
            "Related violations: <none>",
        ])
        violations = tables.parse_violation_blocks(text)
        self.assertEqual(len(violations), 2)
        self.assertEqual(violations[0]["rule"], "NSTD-1")
        self.assertEqual(violations[0]["severity"], "Critical Warning")
        self.assertEqual(violations[0]["title"], "Unspecified I/O Standard")
        self.assertNotIn("Related violations", violations[0]["detail"])
        self.assertEqual(violations[1]["rule"], "TIMING-6")
        self.assertEqual(violations[1]["instance"], 3)

    def test_numeric_cell_forms(self):
        self.assertEqual(tables.to_float("<0.01"), 0.01)
        self.assertAlmostEqual(tables.to_float("90.00"), 90.0)
        self.assertEqual(tables.to_int("1,234"), 1234)
        self.assertIsNone(tables.to_float("NA"))
        self.assertIsNone(tables.to_float(""))

    def test_malformed_input_returns_empty_not_exception(self):
        for text in ("", "+++", "| broken", "---", "no tables here at all"):
            self.assertEqual(tables.parse_pipe_tables(text), [])
            self.assertEqual(tables.parse_violation_blocks(text), [])


class TestReportParsers(unittest.TestCase):
    def test_utilization(self):
        result = reports.parse_utilization(sample("utilization"))
        self.assertTrue(result["available"])
        resources = result["resources"]
        self.assertAlmostEqual(resources["lut"]["util_percent"], 91.0)
        self.assertEqual(resources["lut"]["used"], 185458)
        self.assertAlmostEqual(resources["bram"]["util_percent"], 53.93)
        # The worst resource drives the utilization risk rule.
        self.assertEqual(result["worst"]["name"], "Slice")

    def test_drc(self):
        result = reports.parse_drc(sample("drc"))
        self.assertTrue(result["available"])
        self.assertEqual(result["declared_count"], 4)
        rules = [v["rule"] for v in result["violations"]]
        self.assertEqual(rules, ["NSTD-1", "UCIO-1", "RTSTAT-6", "PLHOLDVIO-2"])
        self.assertEqual(result["by_severity"]["Critical Warning"], 2)

    def test_methodology(self):
        result = reports.parse_methodology(sample("methodology"))
        self.assertTrue(result["available"])
        rules = [v["rule"] for v in result["violations"]]
        self.assertIn("TIMING-6", rules)
        self.assertIn("TIMING-14", rules)

    def test_cdc(self):
        result = reports.parse_cdc(sample("cdc"))
        self.assertTrue(result["available"])
        self.assertEqual(result["by_severity"]["Critical"], 2)
        first = result["findings"][0]
        self.assertEqual(first["id"], "CDC-1")
        self.assertEqual(first["source_clock"], "clk_100")
        self.assertEqual(first["destination_clock"], "clk_200")

    def test_clock_interaction(self):
        result = reports.parse_clock_interaction(sample("clock_interaction"))
        self.assertTrue(result["available"])
        self.assertTrue(result["has_classification"])
        classifications = [p["classification"] for p in result["pairs"]]
        self.assertIn("Timed (Unsafe)", classifications)
        self.assertIn("Safely Timed", classifications)

    def test_control_sets(self):
        result = reports.parse_control_sets(sample("control_sets"))
        self.assertTrue(result["available"])
        self.assertEqual(result["unique_control_sets"], 34210)

    def test_clean_rule_report_is_available_not_unparsed(self):
        # "Violations found: 0" is a real clean result and must be told apart
        # from a report we simply failed to understand.
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "drc.rpt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Report DRC\n\nViolations found: 0\n")
            result = reports.parse_drc(path)
            self.assertTrue(result["available"])
            self.assertEqual(result["violations"], [])
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class TestFailSoftBehaviour(unittest.TestCase):
    """A parser must never raise, and must never fake a clean result."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, content):
        path = os.path.join(self.dir, "report.rpt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_missing_file(self):
        for parser in reports.PARSERS.values():
            result = parser(os.path.join(self.dir, "absent.rpt"))
            self.assertFalse(result["available"])
            self.assertIn("reason", result)

    def test_no_path_given(self):
        for parser in reports.PARSERS.values():
            result = parser(None)
            self.assertFalse(result["available"])

    def test_empty_file(self):
        path = self._write("")
        for parser in reports.PARSERS.values():
            self.assertFalse(parser(path)["available"])

    def test_unrecognised_content(self):
        path = self._write("this is not a Vivado report at all\njust prose\n")
        for name, parser in reports.PARSERS.items():
            result = parser(path)
            self.assertFalse(result["available"],
                             "{0} claimed to parse junk".format(name))

    def test_truncated_report_does_not_raise(self):
        with open(sample("utilization"), encoding="utf-8") as handle:
            full = handle.read()
        for cut in (10, 100, 400, 900):
            path = self._write(full[:cut])
            result = reports.parse_utilization(path)
            self.assertIn("available", result)

    def test_parse_reports_covers_every_kind(self):
        results = reports.parse_reports({})
        self.assertEqual(sorted(results), sorted(reports.PARSERS))
        for result in results.values():
            self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()


class TestCheckReportsTool(unittest.TestCase):
    """The diagnostic users run on their own reports before sending anything."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _install(self, kinds):
        for kind in kinds:
            shutil.copy(sample(kind),
                        os.path.join(self.dir, "{0}_impl_1.rpt".format(kind)))

    def test_exit_zero_when_everything_parses(self):
        import check_reports
        self._install(["timing_summary"] + list(reports.PARSERS))
        self.assertEqual(check_reports.main(["--dir", self.dir]), 0)

    def test_exit_nonzero_when_a_report_fails(self):
        import check_reports
        self._install(["timing_summary"])
        self.assertEqual(check_reports.main(["--dir", self.dir]), 1)

    def test_fingerprint_keeps_structure_and_masks_instance_names(self):
        import check_reports
        path = os.path.join(self.dir, "cdc_impl_1.rpt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join([
                "| Tool Version : Vivado v.2024.2 (lin64) Build 5239630",
                "| Design       : secret_product_top",
                "| Design State : Routed",
                "",
                "+----------+--------+------------------------+",
                "| Severity | CDC ID | Endpoint               |",
                "+----------+--------+------------------------+",
                "| Critical | CDC-1  | u_secret/key_reg[7]/D  |",
                "+----------+--------+------------------------+",
            ]))

        fingerprint = "\n".join(check_reports._fingerprint(path))

        # Layout must survive: it is what the parser has to be fixed against.
        self.assertIn("CDC ID", fingerprint)
        self.assertIn("Severity", fingerprint)
        self.assertIn("Tool Version", fingerprint)
        # Design content must not.
        self.assertNotIn("u_secret", fingerprint)
        self.assertNotIn("key_reg", fingerprint)
        self.assertNotIn("secret_product_top", fingerprint)

    def test_missing_directory_is_an_error_not_a_crash(self):
        import check_reports
        self.assertEqual(
            check_reports.main(["--dir", os.path.join(self.dir, "nope")]), 2)
