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


class TestXprParsing(FileListCase):
    """The .xpr is XML, so the reconciliation needs no Vivado."""

    def test_sample_project(self):
        result = filelist.parse_xpr(
            os.path.join(REPO, "examples", "sample_project.xpr"))
        self.assertIsNotNone(result)
        self.assertEqual(result["top"], "top")

        names = sorted(os.path.basename(p) for p in result["sources"])
        self.assertEqual(names, ["fifo_rx.xci", "pipe.sv", "top.v"])
        self.assertEqual([os.path.basename(p) for p in result["constraints"]],
                         ["main.xdc"])
        self.assertEqual([os.path.basename(p) for p in result["ip"]],
                         ["fifo_rx.xci"])

    def test_simulation_sources_are_not_part_of_the_build(self):
        # tb_top.sv is in sim_1; treating it as a build source would make the
        # reconciliation complain about a file the synthesis never sees.
        result = filelist.parse_xpr(
            os.path.join(REPO, "examples", "sample_project.xpr"))
        self.assertFalse(any("tb_top" in p for p in result["sources"]))
        self.assertIn("sim_1", result["filesets"])

    def test_pprdir_is_resolved_against_the_project_location(self):
        self.write("build/top.xpr", "\n".join([
            '<?xml version="1.0"?>',
            '<Project Version="7">',
            '  <FileSets>',
            '    <FileSet Name="sources_1" Type="DesignSrcs">',
            '      <File Path="$PPRDIR/../rtl/top.v"/>',
            '    </FileSet>',
            '  </FileSets>',
            '</Project>',
        ]))
        result = filelist.parse_xpr(os.path.join(self.dir, "build", "top.xpr"))
        expected = os.path.normpath(os.path.join(self.dir, "rtl", "top.v"))
        self.assertEqual(result["sources"], [expected])

    def test_unreadable_or_wrong_shape_returns_none(self):
        self.assertIsNone(filelist.parse_xpr(
            os.path.join(self.dir, "absent.xpr")))
        self.assertIsNone(filelist.parse_xpr(
            self.write("broken.xpr", "not xml at all <<<")))
        self.assertIsNone(filelist.parse_xpr(
            self.write("empty.xpr", "<?xml version='1.0'?><Project/>")))


class TestModuleScanning(FileListCase):
    def test_declarations_and_instantiations(self):
        top = self.write("top.v", "\n".join([
            "module top (input clk);",
            "  pipe u_pipe (.clk(clk));",
            "  fifo #(.W(8)) u_fifo (.clk(clk));",
            "endmodule",
        ]))
        result = filelist.scan_modules([top])
        self.assertEqual(sorted(result["declared"]), ["top"])
        self.assertEqual(sorted(result["instantiated"]), ["fifo", "pipe"])

    def test_language_keywords_are_not_modules(self):
        # `if (` would otherwise be split into two identifiers by backtracking.
        top = self.write("top.v", "\n".join([
            "module top (input clk, output reg q);",
            "  always @(posedge clk) begin",
            "    if (clk) q <= 1'b0;",
            "    else q <= 1'b1;",
            "  end",
            "  case (clk)",
            "    default: ;",
            "  endcase",
            "endmodule",
        ]))
        self.assertEqual(filelist.scan_modules([top])["instantiated"], {})

    def test_commented_out_instantiations_are_ignored(self):
        top = self.write("top.v", "\n".join([
            "module top;",
            "  // ghost u_ghost (.a(1));",
            "  /* phantom u_phantom (.a(1)); */",
            "  real_mod u_real (.a(1));",
            "endmodule",
        ]))
        self.assertEqual(sorted(filelist.scan_modules([top])["instantiated"]),
                         ["real_mod"])

    def test_vhdl_entity_and_component(self):
        path = self.write("top.vhd", "\n".join([
            "entity top is",
            "end entity;",
            "architecture rtl of top is",
            "  component fifo port (clk : in std_logic); end component;",
            "begin",
            "  u_pipe : entity work.pipe port map (clk => clk);",
            "  -- u_ghost : entity work.ghost port map (clk => clk);",
            "end architecture;",
        ]))
        result = filelist.scan_modules([path])
        self.assertIn("top", result["declared"])
        self.assertIn("fifo", result["instantiated"])
        self.assertIn("pipe", result["instantiated"])
        self.assertNotIn("ghost", result["instantiated"])

    def test_self_reference_is_not_a_dependency(self):
        path = self.write("top.v", "module top (input a); endmodule")
        self.assertNotIn("top", filelist.scan_modules([path])["instantiated"])


class TestMissingModuleClassification(FileListCase):
    """Two tiers, because the two cases warrant very different confidence."""

    def _project(self):
        self.write("rtl/top.v", "\n".join([
            "module top (input clk);",
            "  pipe u_pipe (.clk(clk));",
            "  fifo u_fifo (.clk(clk));",
            "  mystery u_mystery (.clk(clk));",
            "  BUFG u_bufg (.I(clk));",
            "endmodule",
        ]))
        self.write("rtl/pipe.v", "module pipe(input clk); endmodule")
        self.write("rtl/fifo.v", "module fifo(input clk); endmodule")
        return self.write("files.f", "rtl/top.v\nrtl/pipe.v\n")

    def test_module_defined_on_disk_but_not_in_the_build(self):
        listing = self._project()
        result = filelist.check([listing],
                                search_dirs=[os.path.join(self.dir, "rtl")])

        self.assertIn("fifo", result["file_missing"])
        self.assertTrue(any("fifo.v" in p for p in
                            result["file_missing"]["fifo"]["defined_in"]))
        self.assertNotIn("fifo", result["undefined"])

    def test_module_defined_nowhere_is_the_lower_confidence_tier(self):
        listing = self._project()
        result = filelist.check([listing],
                                search_dirs=[os.path.join(self.dir, "rtl")])
        self.assertIn("mystery", result["undefined"])
        self.assertNotIn("mystery", result["file_missing"])

    def test_xilinx_primitives_are_never_reported(self):
        listing = self._project()
        result = filelist.check([listing],
                                search_dirs=[os.path.join(self.dir, "rtl")])
        self.assertNotIn("BUFG", result["undefined"])
        self.assertNotIn("BUFG", result["file_missing"])

    def test_ignore_module_suppresses_a_name(self):
        listing = self._project()
        result = filelist.check([listing],
                                search_dirs=[os.path.join(self.dir, "rtl")],
                                ignore_modules=["mystery"])
        self.assertNotIn("mystery", result["undefined"])

    def test_ip_from_the_xci_counts_as_defined(self):
        self.write("rtl/top.v", "\n".join([
            "module top (input clk);",
            "  fifo_rx u_fifo (.clk(clk));",
            "endmodule",
        ]))
        self.write("ip/fifo_rx/fifo_rx.xci", "{}")
        self.write("build/top.xpr", "\n".join([
            '<?xml version="1.0"?>',
            '<Project Version="7">',
            '  <FileSets>',
            '    <FileSet Name="sources_1" Type="DesignSrcs">',
            '      <File Path="$PPRDIR/../rtl/top.v"/>',
            '      <File Path="$PPRDIR/../ip/fifo_rx/fifo_rx.xci"/>',
            '      <Config><Option Name="TopModule" Val="top"/></Config>',
            '    </FileSet>',
            '  </FileSets>',
            '</Project>',
        ]))
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check(
            [listing], project=os.path.join(self.dir, "build", "top.xpr"))
        self.assertNotIn("fifo_rx", result["undefined"])
        self.assertIn("fifo_rx", result["ignored_modules"])

    def test_a_complete_build_reports_nothing(self):
        self.write("rtl/top.v", "module top; pipe u (.a(1)); endmodule")
        self.write("rtl/pipe.v", "module pipe(input a); endmodule")
        listing = self.write("files.f", "rtl/top.v\nrtl/pipe.v\n")
        result = filelist.check([listing])
        self.assertEqual(result["file_missing"], {})
        self.assertEqual(result["undefined"], {})


class TestTopModule(FileListCase):
    def test_top_found(self):
        self.write("rtl/top.v", "module top; endmodule")
        self.write("build/top.xpr", "\n".join([
            '<?xml version="1.0"?>',
            '<Project Version="7"><FileSets>',
            '  <FileSet Name="sources_1" Type="DesignSrcs">',
            '    <File Path="$PPRDIR/../rtl/top.v"/>',
            '    <Config><Option Name="TopModule" Val="top"/></Config>',
            '  </FileSet>',
            '</FileSets></Project>',
        ]))
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check(
            [listing], project=os.path.join(self.dir, "build", "top.xpr"))
        self.assertTrue(result["top_found"])

    def test_top_missing_is_detected(self):
        self.write("rtl/top.v", "module something_else; endmodule")
        self.write("build/top.xpr", "\n".join([
            '<?xml version="1.0"?>',
            '<Project Version="7"><FileSets>',
            '  <FileSet Name="sources_1" Type="DesignSrcs">',
            '    <File Path="$PPRDIR/../rtl/top.v"/>',
            '    <Config><Option Name="TopModule" Val="top"/></Config>',
            '  </FileSet>',
            '</FileSets></Project>',
        ]))
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check(
            [listing], project=os.path.join(self.dir, "build", "top.xpr"))
        self.assertFalse(result["top_found"])

        assessment = risk_rules.evaluate(filelist=result)
        found = [f for f in assessment["findings"]
                 if f["id"] == "FILELIST.TOP_NOT_FOUND"][0]
        self.assertEqual(found["severity"], risk_rules.BLOCKER)


class TestReconciliationActuallyRuns(FileListCase):
    """Regression: reconcile() existed but nothing ever called it."""

    def test_xpr_drives_the_reconciliation(self):
        self.write("rtl/top.v", "module top; endmodule")
        self.write("rtl/extra.v", "module extra; endmodule")
        self.write("build/top.xpr", "\n".join([
            '<?xml version="1.0"?>',
            '<Project Version="7"><FileSets>',
            '  <FileSet Name="sources_1" Type="DesignSrcs">',
            '    <File Path="$PPRDIR/../rtl/top.v"/>',
            '    <File Path="$PPRDIR/../rtl/extra.v"/>',
            '  </FileSet>',
            '</FileSets></Project>',
        ]))
        listing = self.write("files.f", "rtl/top.v\n")

        result = filelist.check(
            [listing], project=os.path.join(self.dir, "build", "top.xpr"))
        self.assertEqual(result["project_source"], "xpr")
        self.assertFalse(result["reconcile"]["consistent"])
        self.assertEqual(
            [os.path.basename(p)
             for p in result["reconcile"]["only_in_project"]], ["extra.v"])

        assessment = risk_rules.evaluate(filelist=result)
        found = [f for f in assessment["findings"]
                 if f["id"] == "FILELIST.PROJECT_MISMATCH"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["severity"], risk_rules.BLOCKER)

    def test_falls_back_to_the_fileset_dump(self):
        top = self.write("rtl/top.v", "module top; endmodule")
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check([listing], fileset_files=[top],
                               project=os.path.join(self.dir, "absent.xpr"))
        self.assertEqual(result["project_source"], "fileset-list")
        self.assertTrue(result["reconcile"]["consistent"])

    def test_no_project_is_reported_as_not_checked(self):
        # Never silently pass: "the files exist" is not "the project uses them".
        self.write("rtl/top.v", "module top; endmodule")
        listing = self.write("files.f", "rtl/top.v\n")
        result = filelist.check([listing])

        self.assertIsNone(result.get("reconcile"))
        assessment = risk_rules.evaluate(filelist=result)
        found = [f for f in assessment["findings"]
                 if f["id"] == "FILELIST.PROJECT_NOT_CHECKED"][0]
        self.assertEqual(found["severity"], risk_rules.CRITICAL)
        self.assertFalse(assessment["verdict"]["signoff_ok"])
