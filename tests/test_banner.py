# -*- coding: utf-8 -*-
"""The banner must never look greener than the evidence."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "python"))

import banner  # noqa: E402


def verdict(bringup_ok=True, signoff_ok=True, blockers=0, criticals=0,
            warnings=0, waived=0):
    return {
        "counts": {"BLOCKER": blockers, "CRITICAL": criticals,
                   "WARNING": warnings, "INFO": 0},
        "blockers": blockers,
        "criticals": criticals,
        "waived": waived,
        "bringup_ok": bringup_ok,
        "signoff_ok": signoff_ok,
        "bringup_blocker_count": blockers,
        "signoff_blocker_count": blockers + criticals,
    }


class TestClassifyOnStatusAlone(unittest.TestCase):
    """With no verdict file the exit status is all there is."""

    def test_zero_is_pass(self):
        self.assertEqual(banner.classify(0, None)[0], banner.PASS)

    def test_non_zero_is_fail(self):
        result, notes = banner.classify(2, None)
        self.assertEqual(result, banner.FAIL)
        self.assertTrue(any("2" in note for note in notes))


class TestClassifyOnVerdict(unittest.TestCase):

    def test_blocker_with_exit_zero_still_reads_fail(self):
        """The reason this module exists.

        The flow deliberately exits 0 with BLOCKERs -- gating is opt-in via
        -fail-on-blocker. Grading the banner on the exit status would paint a
        green PASS over a design that must not go near hardware.
        """
        result, notes = banner.classify(0, verdict(bringup_ok=False,
                                                   signoff_ok=False,
                                                   blockers=2))
        self.assertEqual(result, banner.FAIL)
        self.assertTrue(any("BLOCKER" in note for note in notes))
        self.assertTrue(any("exit code" in note for note in notes),
                        "should say the exit code did not gate it")

    def test_signoff_blocked_is_warn_not_fail(self):
        result, _ = banner.classify(0, verdict(signoff_ok=False, criticals=1))
        self.assertEqual(result, banner.WARN)

    def test_warnings_only_is_warn(self):
        result, _ = banner.classify(0, verdict(warnings=3))
        self.assertEqual(result, banner.WARN)

    def test_clean_verdict_is_pass(self):
        self.assertEqual(banner.classify(0, verdict())[0], banner.PASS)

    def test_waived_blocker_does_not_fail(self):
        """A waiver clears the flags; the count stays for visibility."""
        result, notes = banner.classify(0, verdict(blockers=1, waived=1))
        self.assertEqual(result, banner.PASS)
        self.assertTrue(any("豁免" in note for note in notes))

    def test_non_zero_status_beats_a_clean_verdict(self):
        result, _ = banner.classify(1, verdict())
        self.assertEqual(result, banner.FAIL)


class TestMissingVerdict(unittest.TestCase):
    """An expected verdict we cannot read is a gap, not a pass."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def test_absent_file_is_warn(self):
        loaded = banner.load_verdict(os.path.join(self.dir, "nope.json"))
        self.assertEqual(loaded, "missing")
        result, notes = banner.classify(0, loaded)
        self.assertEqual(result, banner.WARN)
        self.assertTrue(any("未經檢查" in note for note in notes))

    def test_corrupt_file_is_warn(self):
        path = os.path.join(self.dir, "risk.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        self.assertEqual(banner.load_verdict(path), "missing")

    def test_json_without_a_verdict_block_is_warn(self):
        path = os.path.join(self.dir, "risk.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"findings": []}, handle)
        self.assertEqual(banner.load_verdict(path), "missing")

    def test_no_path_given_means_no_verdict_expected(self):
        self.assertIsNone(banner.load_verdict(None))

    def test_real_verdict_file_round_trips(self):
        path = os.path.join(self.dir, "risk_impl_1.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"verdict": verdict(bringup_ok=False, blockers=1)},
                      handle)
        self.assertEqual(banner.classify(0, banner.load_verdict(path))[0],
                         banner.FAIL)


class TestRendering(unittest.TestCase):

    def test_every_word_has_a_glyph_for_every_letter(self):
        for word in (banner.PASS, banner.WARN, banner.FAIL):
            for letter in word:
                self.assertIn(letter, banner._GLYPHS,
                              "no glyph for '{0}'".format(letter))

    def test_art_is_five_rows_and_uses_the_block_character(self):
        rows = banner.render_word(banner.PASS, "@")
        self.assertEqual(len(rows), 5)
        self.assertIn("@", "".join(rows))
        self.assertNotIn("#", "".join(rows))

    def test_result_word_appears_in_the_output_shape(self):
        text = banner.render(banner.FAIL, "階段 3", ["原因"], "#", "-")
        self.assertIn("階段 3", text)
        self.assertIn("原因", text)
        self.assertIn("#", text)

    def test_result_is_also_plain_text(self):
        """Block letters are unmatchable; logs and CI need the word itself."""
        text = banner.render(banner.FAIL, "階段 3", [], "#", "-")
        self.assertIn("[FAIL]", text)
        self.assertNotIn("[PASS]", text)

    def test_main_passes_the_status_through(self):
        """A banner that swallowed the status would silently ungate the flow."""
        original = sys.stdout
        sys.stdout = io.StringIO()
        try:
            self.assertEqual(banner.main(["--status", "3", "--ascii",
                                          "--no-color"]), 3)
            self.assertEqual(banner.main(["--status", "0", "--ascii",
                                          "--no-color"]), 0)
        finally:
            sys.stdout = original


if __name__ == "__main__":
    unittest.main()
