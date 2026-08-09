# -*- coding: utf-8 -*-
"""Unit tests for the waiver mechanism.

The property that matters most: a waiver must never outlive the finding it was
granted against. If the evidence changes, the exemption has to lapse, or the
mechanism becomes a way to permanently silence a real problem.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import risk_rules        # noqa: E402
import waivers           # noqa: E402


def make_finding(identifier="DRC.CRITICAL_WARNING", rule="RTSTAT-6", count=1):
    return risk_rules.finding(
        identifier, "title", "risk", "action",
        blocks_bringup=True, blocks_signoff=True,
        evidence={"rule": rule, "count": count})


class TestDigest(unittest.TestCase):
    def test_same_evidence_gives_same_digest(self):
        self.assertEqual(waivers.evidence_digest(make_finding()),
                         waivers.evidence_digest(make_finding()))

    def test_changed_evidence_changes_the_digest(self):
        self.assertNotEqual(waivers.evidence_digest(make_finding(count=1)),
                            waivers.evidence_digest(make_finding(count=7)))

    def test_different_rule_id_changes_the_digest(self):
        self.assertNotEqual(
            waivers.evidence_digest(make_finding(identifier="A")),
            waivers.evidence_digest(make_finding(identifier="B")))

    def test_digest_is_independent_of_key_order(self):
        one = risk_rules.finding("X", "t", "r", "a",
                                 evidence={"a": 1, "b": 2})
        two = risk_rules.finding("X", "t", "r", "a",
                                 evidence={"b": 2, "a": 1})
        self.assertEqual(waivers.evidence_digest(one),
                         waivers.evidence_digest(two))


class TestLoading(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, content):
        path = os.path.join(self.dir, "waivers.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_valid_file(self):
        path = self.write(json.dumps({"waivers": [
            {"id": "DRC.WARNING", "reason": "ok", "approved_by": "me"}]}))
        loaded = waivers.load_waivers(path)
        self.assertTrue(loaded["available"])
        self.assertEqual(len(loaded["waivers"]), 1)

    def test_missing_file_waives_nothing(self):
        loaded = waivers.load_waivers(os.path.join(self.dir, "absent.json"))
        self.assertFalse(loaded["available"])
        self.assertEqual(loaded["waivers"], [])

    def test_malformed_json_waives_nothing(self):
        # Failing open here would grant exemptions because a file was corrupt.
        loaded = waivers.load_waivers(self.write("{ not json"))
        self.assertFalse(loaded["available"])
        self.assertEqual(loaded["waivers"], [])
        self.assertIn("nothing waived", loaded["reason"])

    def test_entries_without_an_id_are_dropped(self):
        path = self.write(json.dumps({"waivers": [
            {"reason": "no id here"},
            {"id": "DRC.WARNING", "reason": "fine"},
        ]}))
        loaded = waivers.load_waivers(path)
        self.assertEqual(len(loaded["waivers"]), 1)
        self.assertIn("malformed", loaded["reason"])

    def test_wrong_shape_waives_nothing(self):
        loaded = waivers.load_waivers(self.write(json.dumps({"other": []})))
        self.assertFalse(loaded["available"])


class TestApplying(unittest.TestCase):
    def _apply(self, findings, waiver_entries):
        return waivers.apply_waivers(
            findings, {"available": True, "waivers": waiver_entries})

    def test_matching_waiver_clears_both_gates_but_keeps_severity(self):
        finding = make_finding()
        result = self._apply([finding], [
            {"id": "DRC.CRITICAL_WARNING", "match": {"rule": "RTSTAT-6"},
             "reason": "debug 訊號", "approved_by": "me", "date": "2026-08-09"}])

        self.assertTrue(finding["waived"])
        self.assertFalse(finding["blocks_bringup"])
        self.assertFalse(finding["blocks_signoff"])
        # Severity is untouched: the accepted risk was still a BLOCKER.
        self.assertEqual(finding["severity"], risk_rules.BLOCKER)
        self.assertEqual(finding["waiver"]["approved_by"], "me")
        self.assertEqual(result["applied"], ["DRC.CRITICAL_WARNING"])

    def test_match_criteria_must_all_hold(self):
        finding = make_finding(rule="RTSTAT-6")
        self._apply([finding], [
            {"id": "DRC.CRITICAL_WARNING", "match": {"rule": "NSTD-1"}}])
        self.assertFalse(finding["waived"])

    def test_id_must_match(self):
        finding = make_finding()
        self._apply([finding], [{"id": "SOMETHING.ELSE"}])
        self.assertFalse(finding["waived"])

    def test_pinned_digest_still_matching_applies(self):
        finding = make_finding()
        digest = waivers.evidence_digest(finding)
        self._apply([finding], [
            {"id": "DRC.CRITICAL_WARNING", "evidence_digest": digest}])
        self.assertTrue(finding["waived"])
        self.assertTrue(finding["waiver"]["digest_pinned"])

    def test_waiver_expires_when_the_evidence_changes(self):
        # The central safety property: more violations than were reviewed means
        # the exemption no longer covers what is actually there.
        reviewed = make_finding(count=1)
        stale_digest = waivers.evidence_digest(reviewed)

        worse = make_finding(count=9)
        result = self._apply([worse], [
            {"id": "DRC.CRITICAL_WARNING", "reason": "reviewed at 1",
             "evidence_digest": stale_digest}])

        self.assertFalse(worse["waived"])
        self.assertTrue(worse["blocks_bringup"])
        self.assertEqual(len(result["expired"]), 1)
        self.assertEqual(result["expired"][0]["recorded_digest"], stale_digest)

    def test_unpinned_waiver_accepts_whatever_is_current(self):
        finding = make_finding(count=99)
        self._apply([finding], [{"id": "DRC.CRITICAL_WARNING"}])
        self.assertTrue(finding["waived"])
        self.assertFalse(finding["waiver"]["digest_pinned"])

    def test_every_finding_gets_a_digest_for_pasting_into_a_waiver(self):
        finding = make_finding()
        self._apply([finding], [])
        self.assertEqual(len(finding["digest"]), waivers.DIGEST_LENGTH)


class TestWaiversInAssessment(unittest.TestCase):
    def _timing(self):
        return {"summary": {"whs": -0.05}, "check_timing": {"items": {}},
                "paths": [], "clocks": [{"period_ns": 10.0}]}

    def test_waived_blocker_no_longer_gates_but_is_still_counted(self):
        plain = risk_rules.evaluate(timing=self._timing())
        self.assertFalse(plain["verdict"]["bringup_ok"])
        digest = [f for f in plain["findings"]
                  if f["id"] == "TIMING.HOLD_VIOLATION"][0]["digest"]

        waived = risk_rules.evaluate(
            timing=self._timing(),
            waiver_data={"available": True, "waivers": [
                {"id": "TIMING.HOLD_VIOLATION", "reason": "已評估可接受",
                 "approved_by": "me", "evidence_digest": digest}]})

        self.assertTrue(waived["verdict"]["bringup_ok"])
        self.assertTrue(waived["verdict"]["signoff_ok"])
        self.assertEqual(waived["verdict"]["waived"], 1)
        # Still reported, still a BLOCKER by severity.
        self.assertEqual(waived["verdict"]["counts"][risk_rules.BLOCKER], 1)
        self.assertEqual(waived["waivers"]["applied"], ["TIMING.HOLD_VIOLATION"])

    def test_expired_waiver_is_reported_in_the_assessment(self):
        assessment = risk_rules.evaluate(
            timing=self._timing(),
            waiver_data={"available": True, "waivers": [
                {"id": "TIMING.HOLD_VIOLATION",
                 "evidence_digest": "0000deadbeef"}]})
        self.assertFalse(assessment["verdict"]["bringup_ok"])
        self.assertEqual(len(assessment["waivers"]["expired"]), 1)

    def test_no_waiver_data_changes_nothing(self):
        assessment = risk_rules.evaluate(timing=self._timing())
        self.assertFalse(assessment["verdict"]["bringup_ok"])
        self.assertEqual(assessment["waivers"]["applied"], [])


if __name__ == "__main__":
    unittest.main()
