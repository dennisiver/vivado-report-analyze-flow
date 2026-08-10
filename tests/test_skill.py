# -*- coding: utf-8 -*-
"""Keep the bundled Agent Skill honest about what the code actually does.

A skill that documents the risk model is only useful while it agrees with
``risk_rules.py``. Documentation drift is normally a papercut; here it is worse
than having no skill at all, because the skill is consulted to make safety
calls -- "is this bitstream safe to take to hardware". A renamed rule or a new
BLOCKER the skill never mentions turns it into a confident liar.

So the rule inventory is derived from the source rather than maintained by
hand, and reconciled against the skill in both directions.
"""

import ast
import os
import re
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))

import risk_rules        # noqa: E402

SKILL_DIR = os.path.join(REPO, "skills", "vivado-fpga-flow")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")
REFERENCES = os.path.join(SKILL_DIR, "references")

RISK_RULES_SOURCE = os.path.join(REPO, "python", "risk_rules.py")

# The skill body is loaded in full every time the skill triggers, so it is held
# to the same discipline as the agent-facing summaries the flow generates.
MAX_BODY_LINES = 200
# The description is loaded on *every* turn, whether or not the skill is used.
MAX_DESCRIPTION_CHARS = 1400

_RULE_ID = re.compile(r"\b([A-Z][A-Z0-9]*\.[A-Z0-9_]+)\b")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


# ast.parse only produces Constant nodes from Python 3.8 on. Under 3.6/3.7 a
# string literal is ast.Str and True is ast.NameConstant, so matching Constant
# alone extracted nothing at all: the inventory collapsed to the six LOG.* ids
# built from the runtime table, and the reconciliation then compared the skill
# docs against almost no code. This repo targets Python 3.4+, and the tests have
# to honour that too -- an offline workstation is exactly where the old
# interpreter lives.
#
# ast.Str was removed in 3.12, hence getattr rather than a direct reference.
# Constant is checked first because on 3.8-3.11 ast.Str has a custom
# __instancecheck__ that also matches Constant nodes.
_AST_STR = getattr(ast, "Str", ())
_AST_NAME_CONSTANT = getattr(ast, "NameConstant", ())


def string_literal(node):
    """The str value of a literal node, on any supported Python."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if _AST_STR and isinstance(node, _AST_STR):
        return node.s
    return None


def is_true_literal(node):
    """Whether a node is the literal ``True``, on any supported Python."""
    if isinstance(node, ast.Constant):
        return node.value is True
    if _AST_NAME_CONSTANT and isinstance(node, _AST_NAME_CONSTANT):
        return node.value is True
    return False


def collect_rules():
    """Every rule id ``risk_rules`` can emit, and whether it blocks bring-up.

    Derived from the ``finding(...)`` calls by AST so it cannot fall behind the
    code. The LOG.* ids are built at runtime from a table, so they are expanded
    from that table rather than read as literals.
    """
    tree = ast.parse(_read(RISK_RULES_SOURCE))
    rules = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "finding"):
            continue
        if not node.args:
            continue

        blocks_bringup = any(
            keyword.arg == "blocks_bringup" and is_true_literal(keyword.value)
            for keyword in node.keywords)

        first = node.args[0]
        identifier = string_literal(first)
        if identifier is not None:
            rules[identifier] = blocks_bringup
        elif isinstance(first, ast.Call):
            # "LOG.{0}".format(key) -- expand over the curated message classes.
            for key in risk_rules._LOG_CLASS_RULES:
                identifier = "LOG.{0}".format(key)
                entry = risk_rules._LOG_CLASS_RULES[key]
                rules[identifier] = entry.get("blocks_bringup", False)

    return rules


def skill_text():
    """SKILL.md plus every reference, as one blob for mention checks."""
    parts = [_read(SKILL_MD)]
    for name in sorted(os.listdir(REFERENCES)):
        if name.endswith(".md"):
            parts.append(_read(os.path.join(REFERENCES, name)))
    return "\n".join(parts)


def split_frontmatter(text):
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end < 0:
        return None, text
    return text[3:end].strip(), text[end + 4:]


class TestSkillStructure(unittest.TestCase):
    def test_skill_and_references_exist(self):
        self.assertTrue(os.path.isfile(SKILL_MD))
        self.assertTrue(os.path.isdir(REFERENCES))
        self.assertTrue([n for n in os.listdir(REFERENCES) if n.endswith(".md")])

    def test_frontmatter_has_name_and_description(self):
        frontmatter, _ = split_frontmatter(_read(SKILL_MD))
        self.assertIsNotNone(frontmatter, "SKILL.md has no YAML frontmatter")
        self.assertIn("name:", frontmatter)
        self.assertIn("description:", frontmatter)

    def test_name_matches_the_directory_and_is_well_formed(self):
        frontmatter, _ = split_frontmatter(_read(SKILL_MD))
        match = re.search(r"^name:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
        self.assertIsNotNone(match, "no parseable name in frontmatter")
        name = match.group(1)
        self.assertRegex(name, r"^[a-z][a-z0-9-]*$")
        self.assertEqual(name, os.path.basename(SKILL_DIR))

    def test_description_is_bounded(self):
        # It costs context on every turn, used or not.
        frontmatter, _ = split_frontmatter(_read(SKILL_MD))
        description = frontmatter.split("description:", 1)[1]
        self.assertLess(len(description), MAX_DESCRIPTION_CHARS)

    def test_description_names_concrete_triggers(self):
        frontmatter, _ = split_frontmatter(_read(SKILL_MD))
        lowered = frontmatter.lower()
        for trigger in ("vivado", "timing", "cdc", "wns", "sign"):
            self.assertIn(trigger, lowered,
                          "description should mention '{0}'".format(trigger))

    def test_body_stays_short_enough_to_load_every_time(self):
        _, body = split_frontmatter(_read(SKILL_MD))
        self.assertLess(len(body.splitlines()), MAX_BODY_LINES)

    def test_every_reference_is_pointed_at_from_the_body(self):
        # An unreferenced file is dead weight: nothing would ever load it.
        body = _read(SKILL_MD)
        for name in sorted(os.listdir(REFERENCES)):
            if name.endswith(".md"):
                self.assertIn(name, body,
                              "{0} is never referenced from SKILL.md".format(name))

    def test_body_does_not_point_at_missing_references(self):
        body = _read(SKILL_MD)
        for mentioned in set(re.findall(r"references/([\w.-]+\.md)", body)):
            self.assertTrue(
                os.path.isfile(os.path.join(REFERENCES, mentioned)),
                "SKILL.md points at references/{0} which does not exist".format(
                    mentioned))


class TestExtractionWorksOnOlderPython(unittest.TestCase):
    """The workstation runs whatever python3 the OS shipped, often 3.6.

    Everything else in this file is downstream of the literal extraction, so if
    it silently returns nothing on an older interpreter the whole reconciliation
    turns into a comparison against an empty set.
    """

    def test_constant_nodes_are_read(self):
        node = ast.parse("f('RULE.ID')").body[0].value.args[0]
        self.assertEqual(string_literal(node), "RULE.ID")

    def test_true_keyword_is_recognised(self):
        node = ast.parse("f(x=True)").body[0].value.keywords[0].value
        self.assertTrue(is_true_literal(node))
        node = ast.parse("f(x=False)").body[0].value.keywords[0].value
        self.assertFalse(is_true_literal(node))

    @unittest.skipUnless(_AST_STR, "ast.Str removed in this Python")
    def test_pre_3_8_string_nodes_are_read(self):
        """What 3.6/3.7 hand back from ast.parse."""
        node = _AST_STR(s="RULE.ID")
        self.assertEqual(string_literal(node), "RULE.ID")

    @unittest.skipUnless(_AST_NAME_CONSTANT,
                         "ast.NameConstant removed in this Python")
    def test_pre_3_8_true_nodes_are_read(self):
        node = _AST_NAME_CONSTANT(value=True)
        self.assertTrue(is_true_literal(node))
        self.assertFalse(is_true_literal(_AST_NAME_CONSTANT(value=False)))

    def test_non_literals_return_none_rather_than_guessing(self):
        node = ast.parse("f(name)").body[0].value.args[0]
        self.assertIsNone(string_literal(node))
        self.assertFalse(is_true_literal(node))


class TestRuleInventoryReconciliation(unittest.TestCase):
    """Both directions, so neither renames nor additions can slip through."""

    def setUp(self):
        self.rules = collect_rules()
        self.text = skill_text()
        self.mentioned = set(_RULE_ID.findall(self.text))

    def test_the_inventory_was_actually_extracted(self):
        # Guard against the AST walk silently finding nothing and every other
        # assertion in this class passing vacuously. It has already earned its
        # keep once: on Python 3.6/3.7 the literal path matched nothing and the
        # inventory shrank to just the LOG.* ids, which this caught.
        self.assertGreater(
            len(self.rules), 30,
            "only {0} rules extracted. If that number is 6 (the LOG.* table "
            "alone), the AST literal match failed -- check this interpreter "
            "({1}) against string_literal() above.".format(
                len(self.rules), sys.version.split()[0]))
        self.assertIn("TIMING.HOLD_VIOLATION", self.rules)
        self.assertIn("LOG.CONSTRAINT_NOT_APPLIED", self.rules)

    def test_every_rule_the_skill_mentions_exists_in_the_code(self):
        # Catches a rule that was renamed or removed while the skill kept
        # describing the old one.
        known = set(self.rules)
        # Rule-shaped tokens that are not rule ids (severity words, file names).
        ignore = {"SKILL.MD", "README.MD"}
        unknown = sorted(
            identifier for identifier in self.mentioned
            if identifier not in known and identifier.upper() not in ignore)
        self.assertEqual(unknown, [],
                         "skill mentions rule ids that risk_rules.py does not "
                         "define: {0}".format(unknown))

    def test_every_blocking_rule_is_documented(self):
        # Catches a newly added BLOCKER the skill never tells anyone about --
        # the failure mode that would make the skill unsafe to rely on.
        blocking = set(identifier for identifier, blocks in self.rules.items()
                       if blocks)
        undocumented = sorted(blocking - self.mentioned)
        self.assertEqual(undocumented, [],
                         "these rules block hardware bring-up but are not "
                         "mentioned anywhere in the skill: {0}".format(
                             undocumented))

    def test_the_reconciliation_would_actually_catch_a_gap(self):
        # A test that can never fail is worse than no test. Simulate a new
        # blocking rule and confirm the check above would flag it.
        pretend = dict(self.rules)
        pretend["TIMING.BRAND_NEW_BLOCKER"] = True
        undocumented = sorted(
            identifier for identifier, blocks in pretend.items()
            if blocks and identifier not in self.mentioned)
        self.assertEqual(undocumented, ["TIMING.BRAND_NEW_BLOCKER"])


class TestDocumentedFactsMatchBehaviour(unittest.TestCase):
    """Spot-check the claims the skill leans on hardest against real output."""

    def _timing(self, summary, checks=None):
        return {"summary": summary, "check_timing": {"items": checks or {}},
                "paths": [], "clocks": [{"period_ns": 10.0}]}

    def _ids(self, assessment):
        return dict((f["id"], f["severity"]) for f in assessment["findings"])

    def test_hold_blocks_at_impl_and_is_absent_at_synth(self):
        timing = self._timing({"whs": -0.05})
        impl = self._ids(risk_rules.evaluate(timing=timing, stage_kind="impl"))
        synth = self._ids(risk_rules.evaluate(timing=timing, stage_kind="synth"))

        self.assertEqual(impl.get("TIMING.HOLD_VIOLATION"), risk_rules.BLOCKER)
        self.assertNotIn("TIMING.HOLD_VIOLATION", synth)

    def test_setup_is_critical_at_impl_and_a_warning_at_synth(self):
        timing = self._timing({"wns": -0.234})
        impl = self._ids(risk_rules.evaluate(timing=timing, stage_kind="impl"))
        synth = self._ids(risk_rules.evaluate(timing=timing, stage_kind="synth"))

        self.assertEqual(impl.get("TIMING.SETUP_VIOLATION"), risk_rules.CRITICAL)
        self.assertEqual(synth.get("TIMING.SETUP_VIOLATION_SYNTH"),
                         risk_rules.WARNING)

    def test_constraint_not_applied_is_a_blocker_despite_being_a_warning(self):
        # The skill makes a point of this; if it ever stopped being true the
        # skill's advice would be actively misleading.
        logs = {"available": True, "messages": [
            {"id": "Vivado 12-507", "severity": "WARNING", "count": 1,
             "examples": ["No objects matched 'get_ports clk'"],
             "risk_class": "CONSTRAINT_NOT_APPLIED"}]}
        found = self._ids(risk_rules.evaluate(logs=logs))
        self.assertEqual(found.get("LOG.CONSTRAINT_NOT_APPLIED"),
                         risk_rules.BLOCKER)

    def test_severity_ladder_matches_what_the_skill_documents(self):
        self.assertEqual(risk_rules.severity_of(True, True), risk_rules.BLOCKER)
        self.assertEqual(risk_rules.severity_of(False, True), risk_rules.CRITICAL)
        self.assertEqual(risk_rules.severity_of(False, False), risk_rules.WARNING)

    def test_documented_thresholds_match_the_defaults(self):
        # The reference file tabulates these numbers, so a silent change to the
        # defaults would leave the skill quoting thresholds nobody is using.
        # Compared numerically: "0.10" and 0.1 are the same threshold.
        text = _read(os.path.join(REFERENCES, "risk-model.md"))
        documented = dict(
            (key, float(value)) for key, value in
            re.findall(r"^\|\s*`(\w+)`\s*\|\s*([\d.]+)\s*\|", text, re.MULTILINE))

        self.assertTrue(documented, "no threshold table found in risk-model.md")
        for key, value in documented.items():
            self.assertIn(key, risk_rules.DEFAULT_THRESHOLDS,
                          "risk-model.md documents an unknown threshold "
                          "'{0}'".format(key))
            self.assertAlmostEqual(
                float(risk_rules.DEFAULT_THRESHOLDS[key]), value,
                msg="DEFAULT_THRESHOLDS[{0}] is {1} but risk-model.md says "
                    "{2}".format(key, risk_rules.DEFAULT_THRESHOLDS[key], value))

    def test_every_threshold_is_documented(self):
        text = _read(os.path.join(REFERENCES, "risk-model.md"))
        missing = sorted(key for key in risk_rules.DEFAULT_THRESHOLDS
                         if "`{0}`".format(key) not in text)
        self.assertEqual(missing, [],
                         "thresholds missing from references/risk-model.md: "
                         "{0}".format(missing))


class TestReadingRulesAreStated(unittest.TestCase):
    """The guardrails that stop an agent from blowing up its own context."""

    def test_body_tells_the_agent_what_not_to_read(self):
        body = _read(SKILL_MD)
        self.assertIn("raw/", body)
        self.assertIn("signoff", body)
        self.assertIn("latest_flow.md", body)

    def test_body_covers_the_prerequisite(self):
        body = _read(SKILL_MD)
        self.assertIn("config.mk", body)


if __name__ == "__main__":
    unittest.main()


class TestStatedCountsAreAccurate(unittest.TestCase):
    """A number in the docs is a claim, and claims drift silently."""

    def test_blocker_count_in_the_heading_matches_the_code(self):
        rules = collect_rules()
        actual = sum(1 for blocks in rules.values() if blocks)
        text = _read(os.path.join(REFERENCES, "risk-model.md"))

        match = re.search(r"BLOCKER[，,]\s*(\d+)\s*條", text)
        self.assertIsNotNone(match, "risk-model.md no longer states a count")
        self.assertEqual(int(match.group(1)), actual,
                         "risk-model.md says {0} blocking rules, code has "
                         "{1}".format(match.group(1), actual))

    def test_total_rule_count_in_skill_md_matches_the_code(self):
        """This one had already drifted (said 47, code had 57).

        The reconciliation tests check that every rule ID is *mentioned*
        somewhere, which a stale total sails straight through -- so the total
        needs its own check.
        """
        actual = len(collect_rules())
        text = _read(SKILL_MD)

        match = re.search(r"全部\s*(\d+)\s*條\s*rule ID", text)
        self.assertIsNotNone(match, "SKILL.md no longer states a rule total")
        self.assertEqual(int(match.group(1)), actual,
                         "SKILL.md says {0} rules, code has {1}".format(
                             match.group(1), actual))

    def test_every_blocking_rule_is_in_the_blocker_table(self):
        # Being mentioned anywhere satisfies the reconciliation test; a reader
        # looking up "what blocks bring-up" needs them in that one table.
        rules = collect_rules()
        text = _read(os.path.join(REFERENCES, "risk-model.md"))
        section = text.split("### 阻擋上板")[1].split("### 阻擋 sign-off")[0]

        missing = sorted(name for name, blocks in rules.items()
                         if blocks and "`{0}`".format(name) not in section)
        self.assertEqual(missing, [],
                         "blocking rules absent from the BLOCKER table: "
                         "{0}".format(missing))
