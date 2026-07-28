# -*- coding: utf-8 -*-
"""Unit tests for input-manifest hashing and change detection."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import manifest  # noqa: E402


def write(path, content):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


class TestPorcelainParsing(unittest.TestCase):
    """The status column is fixed-width; a stray strip() silently eats it."""

    def test_modified_and_untracked_paths(self):
        status = " M xdc/main.xdc\n?? rtl/new.v\nA  rtl/added.v\n"
        self.assertEqual(manifest._parse_porcelain(status),
                         ["xdc/main.xdc", "rtl/new.v", "rtl/added.v"])

    def test_rename_uses_destination_path(self):
        self.assertEqual(
            manifest._parse_porcelain("R  rtl/old.v -> rtl/new.v\n"),
            ["rtl/new.v"])

    def test_quoted_path_is_unquoted(self):
        self.assertEqual(
            manifest._parse_porcelain('?? "rtl/has space.v"\n'),
            ["rtl/has space.v"])

    def test_empty_status(self):
        self.assertEqual(manifest._parse_porcelain(""), [])
        self.assertEqual(manifest._parse_porcelain(None), [])


class TestManifestComparison(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.rtl = write(os.path.join(self.dir, "rtl", "top.v"), "module top;")
        self.xdc = write(os.path.join(self.dir, "xdc", "main.xdc"), "create_clock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _build(self):
        return manifest.build_manifest("impl_1", [self.rtl], [self.xdc])

    def test_identical_inputs_are_not_a_change(self):
        first = self._build()
        second = self._build()
        comparison = manifest.compare_manifests(first, second)
        self.assertFalse(comparison["changed"])

    def test_mtime_change_alone_is_not_a_change(self):
        # Touching a file without editing it (a checkout, an rsync) must not
        # trigger a needless full rebuild.
        first = self._build()
        os.utime(self.rtl, (0, 0))
        comparison = manifest.compare_manifests(first, self._build())
        self.assertFalse(comparison["changed"])

    def test_edited_constraint_is_detected(self):
        first = self._build()
        write(self.xdc, "create_clock\nset_input_delay 1")
        comparison = manifest.compare_manifests(first, self._build())
        self.assertTrue(comparison["changed"])
        self.assertEqual([os.path.basename(p)
                          for p in comparison["constraints"]["modified"]],
                         ["main.xdc"])
        self.assertFalse(comparison["sources"]["changed"])

    def test_added_and_removed_files_are_detected(self):
        first = self._build()
        extra = write(os.path.join(self.dir, "rtl", "extra.v"), "module extra;")
        second = manifest.build_manifest("impl_1", [self.rtl, extra], [self.xdc])
        comparison = manifest.compare_manifests(first, second)
        self.assertTrue(comparison["changed"])
        self.assertEqual([os.path.basename(p)
                          for p in comparison["sources"]["added"]], ["extra.v"])

        back = manifest.compare_manifests(second, first)
        self.assertEqual([os.path.basename(p)
                          for p in back["sources"]["removed"]], ["extra.v"])

    def test_first_run_counts_as_changed(self):
        comparison = manifest.compare_manifests(None, self._build())
        self.assertTrue(comparison["changed"])
        self.assertTrue(comparison["first_run"])

    def test_digest_is_order_independent(self):
        extra = write(os.path.join(self.dir, "rtl", "extra.v"), "module extra;")
        one = manifest.build_manifest("impl_1", [self.rtl, extra], [])
        two = manifest.build_manifest("impl_1", [extra, self.rtl], [])
        self.assertEqual(one["digest"]["sources"], two["digest"]["sources"])

    def test_missing_file_is_recorded_not_fatal(self):
        result = manifest.build_manifest(
            "impl_1", [os.path.join(self.dir, "gone.v")], [])
        record = result["files"]["sources"][0]
        self.assertIsNone(record["sha256"])
        self.assertTrue(record.get("missing"))


class TestGitProvenance(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.rtl = write(os.path.join(self.dir, "rtl", "top.v"), "module top;")
        self.xdc = write(os.path.join(self.dir, "xdc", "main.xdc"), "create_clock")
        self.have_git = self._git_init()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _git(self, *args):
        return subprocess.call(
            ["git"] + list(args), cwd=self.dir,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _git_init(self):
        try:
            if self._git("init", "-q", ".") != 0:
                return False
        except OSError:
            return False
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "test")
        self._git("add", "-A")
        self._git("commit", "-qm", "init")
        return True

    def test_clean_tree_reports_commit_and_no_dirty_inputs(self):
        if not self.have_git:
            self.skipTest("git unavailable")
        result = manifest.build_manifest(
            "impl_1", [self.rtl], [self.xdc], repo_root=self.dir)
        git = result["git"]
        self.assertTrue(git["available"])
        self.assertEqual(len(git["commit"]), 40)
        self.assertEqual(git["dirty_inputs"], [])

    def test_uncommitted_design_file_is_flagged(self):
        if not self.have_git:
            self.skipTest("git unavailable")
        write(self.xdc, "create_clock\nset_max_delay 5")
        result = manifest.build_manifest(
            "impl_1", [self.rtl], [self.xdc], repo_root=self.dir)

        self.assertEqual([os.path.basename(p)
                          for p in result["git"]["dirty_inputs"]], ["main.xdc"])
        constraint = result["files"]["constraints"][0]
        self.assertTrue(constraint["git_dirty"])
        self.assertNotIn("git_dirty", result["files"]["sources"][0])

    def test_unrelated_dirty_file_is_not_flagged_as_an_input(self):
        if not self.have_git:
            self.skipTest("git unavailable")
        write(os.path.join(self.dir, "notes.txt"), "scratch")
        result = manifest.build_manifest(
            "impl_1", [self.rtl], [self.xdc], repo_root=self.dir)
        self.assertTrue(result["git"]["dirty"])
        self.assertEqual(result["git"]["dirty_inputs"], [])

    def test_non_repo_is_reported_as_unavailable(self):
        plain = tempfile.mkdtemp()
        try:
            info = manifest.git_info(plain)
            self.assertFalse(info["available"])
        finally:
            shutil.rmtree(plain, ignore_errors=True)


class TestManifestCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.out = os.path.join(self.dir, "timing_analysis")
        self.rtl = write(os.path.join(self.dir, "rtl", "top.v"), "module top;")
        self.xdc = write(os.path.join(self.dir, "xdc", "main.xdc"), "create_clock")
        self.sources_list = write(os.path.join(self.dir, "src.list"), self.rtl + "\n")
        self.xdc_list = write(os.path.join(self.dir, "con.list"), self.xdc + "\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _run(self, save):
        args = ["--stage", "impl_1", "--outdir", self.out,
                "--sources-list", self.sources_list,
                "--constraints-list", self.xdc_list]
        if save:
            args.append("--save")
        return manifest.main(args)

    def test_compare_only_does_not_write_a_baseline(self):
        self.assertEqual(self._run(save=False), 0)
        self.assertFalse(os.path.exists(
            os.path.join(self.out, "manifests", "manifest_impl_1_current.json")))

    def test_save_writes_baseline_and_compare_artifacts(self):
        self._run(save=True)
        self.assertTrue(os.path.isfile(
            os.path.join(self.out, "manifests", "manifest_impl_1_current.json")))
        compare = manifest.load_json(manifest.compare_path(self.out, "impl_1"))
        self.assertTrue(compare["comparison"]["first_run"])

    def test_baseline_tracks_the_last_saved_run(self):
        self._run(save=True)
        write(self.xdc, "create_clock\nset_input_delay 1")

        # An unsaved comparison must not move the baseline, so a subsequent
        # run still sees the edit rather than believing it was already built.
        self._run(save=False)
        self._run(save=False)
        compare = manifest.load_json(manifest.compare_path(self.out, "impl_1"))
        self.assertTrue(compare["comparison"]["first_run"])

        self._run(save=True)
        compare = manifest.load_json(manifest.compare_path(self.out, "impl_1"))
        self.assertTrue(compare["comparison"]["changed"])
        self.assertEqual([os.path.basename(p) for p in
                          compare["comparison"]["constraints"]["modified"]],
                         ["main.xdc"])


if __name__ == "__main__":
    unittest.main()
