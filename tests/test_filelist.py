# -*- coding: utf-8 -*-
"""Unit tests for the static file list checks (stage 1, no Vivado needed)."""

import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import filelist          # noqa: E402
import risk_rules        # noqa: E402


class FileListCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, relative, content=""):
        path = os.path.join(self.dir, relative)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path


class TestDotFParsing(FileListCase):
    def test_paths_comments_and_incdirs(self):
        self.write("rtl/top.v", "module top; endmodule")
        self.write("rtl/include/defs.vh", "")
        listing = self.write("files.f", "\n".join([
            "# 這是註解",
            "+incdir+./rtl/include",
            "rtl/top.v      // 行尾註解",
            "",
        ]))
        parsed = filelist.parse_filelist(listing)
        self.assertEqual([os.path.basename(p) for p in parsed["files"]],
                         ["top.v"])
        self.assertEqual([os.path.basename(p) for p in parsed["incdirs"]],
                         ["include"])
        self.assertEqual(parsed["errors"], [])

    def test_paths_resolve_relative_to_their_own_list(self):
        # Standard .f semantics: a nested list's paths are relative to it, not
        # to the top-level list or the working directory.
        self.write("rtl/core/pipe.sv", "module pipe; endmodule")
        self.write("rtl/core/core.f", "pipe.sv\n")
        listing = self.write("files.f", "-f rtl/core/core.f\n")

        parsed = filelist.parse_filelist(listing)
        self.assertEqual(len(parsed["files"]), 1)
        self.assertTrue(parsed["files"][0].endswith("rtl/core/pipe.sv"))
        self.assertTrue(os.path.isfile(parsed["files"][0]))

    def test_nested_lists_are_followed(self):
        self.write("a.v")
        self.write("b.v")
        self.write("nested.f", "b.v\n")
        listing = self.write("files.f", "a.v\n-f nested.f\n")
        parsed = filelist.parse_filelist(listing)
        self.assertEqual(sorted(os.path.basename(p) for p in parsed["files"]),
                         ["a.v", "b.v"])

    def test_cyclic_include_terminates(self):
        self.write("one.f", "-f two.f\n")
        self.write("two.f", "-f one.f\n")
        parsed = filelist.parse_filelist(os.path.join(self.dir, "one.f"))
        self.assertEqual(parsed["files"], [])

    def test_defines_and_unknown_switches_are_ignored_as_files(self):
        self.write("a.v")
        listing = self.write("files.f", "+define+FOO=1\n-sverilog\na.v\n")
        parsed = filelist.parse_filelist(listing)
        self.assertEqual([os.path.basename(p) for p in parsed["files"]], ["a.v"])
        self.assertEqual(parsed["defines"], ["FOO=1"])

    def test_missing_list_is_reported_not_raised(self):
        parsed = filelist.parse_filelist(os.path.join(self.dir, "absent.f"))
        self.assertTrue(parsed["errors"])
        self.assertEqual(parsed["files"], [])


class TestTclParsing(FileListCase):
    def test_add_files_and_read_commands(self):
        self.write("rtl/top.v")
        self.write("constrs/main.xdc")
        listing = self.write("files.tcl", "\n".join([
            "# comment",
            "add_files -fileset sources_1 rtl/top.v",
            "read_xdc constrs/main.xdc",
            "set_property top top [current_fileset]",
        ]))
        parsed = filelist.parse_filelist(listing)
        names = sorted(os.path.basename(p) for p in parsed["files"])
        self.assertEqual(names, ["main.xdc", "top.v"])
        # The -fileset flag and its value must not be taken for a file.
        self.assertFalse(any("sources_1" in p for p in parsed["files"]))

    def test_braced_and_quoted_arguments(self):
        self.write("rtl/a b.v")
        listing = self.write("files.tcl", 'add_files {rtl/a b.v}\n')
        parsed = filelist.parse_filelist(listing)
        self.assertEqual([os.path.basename(p) for p in parsed["files"]],
                         ["a b.v"])


class TestChecks(FileListCase):
    def test_missing_files_and_incdirs_are_found(self):
        self.write("rtl/present.v")
        listing = self.write("files.f", "\n".join([
            "+incdir+./rtl/nowhere",
            "rtl/present.v",
            "rtl/absent.v",
        ]))
        result = filelist.check([listing])
        self.assertEqual([os.path.basename(p)
                          for p in result["missing_files"]], ["absent.v"])
        self.assertEqual(len(result["missing_incdirs"]), 1)
        self.assertEqual(result["file_count"], 2)

    def test_reconcile_reports_both_directions(self):
        shared = self.write("rtl/shared.v")
        only_list = self.write("rtl/only_list.v")
        only_project = self.write("rtl/only_project.v")
        listing = self.write("files.f", "rtl/shared.v\nrtl/only_list.v\n")

        result = filelist.check([listing], [shared, only_project])
        reconciliation = result["reconcile"]
        self.assertFalse(reconciliation["consistent"])
        self.assertEqual([os.path.basename(p)
                          for p in reconciliation["only_in_filelist"]],
                         ["only_list.v"])
        self.assertEqual([os.path.basename(p)
                          for p in reconciliation["only_in_project"]],
                         ["only_project.v"])
        self.assertTrue(only_list)

    def test_reconcile_consistent_when_they_agree(self):
        path = self.write("rtl/top.v")
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check([listing], [path])
        self.assertTrue(result["reconcile"]["consistent"])

    def test_duplicate_module_detection(self):
        self.write("rtl/a.v", "module pipe;\nendmodule\n")
        self.write("rtl/b.sv", "module pipe;\nendmodule\n")
        self.write("rtl/c.v", "module other;\nendmodule\n")
        listing = self.write("files.f", "rtl/a.v\nrtl/b.sv\nrtl/c.v\n")

        duplicates = filelist.check([listing])["duplicate_modules"]
        self.assertEqual(sorted(duplicates), ["pipe"])
        self.assertEqual(len(duplicates["pipe"]), 2)

    def test_vhdl_entity_counts_as_a_declaration(self):
        self.write("rtl/a.vhd", "entity core is\nend entity;\n")
        self.write("rtl/b.vhd", "entity core is\nend entity;\n")
        listing = self.write("files.f", "rtl/a.vhd\nrtl/b.vhd\n")
        self.assertIn("core", filelist.check([listing])["duplicate_modules"])


class TestFileListRiskGrading(FileListCase):
    def test_missing_file_is_a_blocker(self):
        listing = self.write("files.f", "rtl/absent.v\n")
        result = filelist.check([listing])
        assessment = risk_rules.evaluate(filelist=result)

        found = [f for f in assessment["findings"]
                 if f["id"] == "FILELIST.MISSING_FILE"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], risk_rules.BLOCKER)
        self.assertFalse(assessment["verdict"]["bringup_ok"])

    def test_project_mismatch_is_a_blocker(self):
        path = self.write("rtl/top.v")
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check([listing], [path, self.write("rtl/extra.v")])
        assessment = risk_rules.evaluate(filelist=result)

        found = [f for f in assessment["findings"]
                 if f["id"] == "FILELIST.PROJECT_MISMATCH"]
        self.assertEqual(found[0]["severity"], risk_rules.BLOCKER)

    def test_clean_list_produces_no_findings(self):
        path = self.write("rtl/top.v", "module top; endmodule")
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check([listing], [path])
        assessment = risk_rules.evaluate(filelist=result)
        self.assertEqual([f for f in assessment["findings"]
                          if f["id"].startswith("FILELIST.")], [])

    def test_duplicate_module_blocks_signoff_only(self):
        self.write("rtl/a.v", "module pipe;\nendmodule\n")
        self.write("rtl/b.v", "module pipe;\nendmodule\n")
        listing = self.write("files.f", "rtl/a.v\nrtl/b.v\n")
        assessment = risk_rules.evaluate(filelist=filelist.check([listing]))

        found = [f for f in assessment["findings"]
                 if f["id"] == "FILELIST.DUPLICATE_MODULE"][0]
        self.assertEqual(found["severity"], risk_rules.CRITICAL)
        self.assertTrue(assessment["verdict"]["bringup_ok"])


if __name__ == "__main__":
    unittest.main()
