#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print a large PASS / WARN / FAIL banner at the end of a make target.

The point is not decoration. This flow deliberately exits 0 even when it has
found BLOCKERs -- gating is opt-in via ``-fail-on-blocker`` so that adding the
risk grading could never silently change the behaviour of an existing script.
That design has one bad consequence for a human watching the terminal: the exit
status alone is not the verdict, so a green "PASS" driven by exit code would be
a lie exactly when it matters most.

So the banner reads the stage's ``risk_<stage>.json`` when there is one and
grades on the verdict, falling back to the exit status when there is not:

    exit != 0                       -> FAIL
    verdict says bring-up blocked   -> FAIL   (even though the exit status is 0)
    verdict says sign-off blocked   -> WARN
    verdict file expected, missing  -> WARN   (never PASS on evidence we lack)
    otherwise                       -> PASS

Stdlib only, and written for the oldest Python likely to be sitting on an
offline workstation, hence ``.format()`` rather than f-strings.
"""

from __future__ import print_function

import argparse
import json
import os
import sys

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

_COLOURS = {PASS: "32", WARN: "33", FAIL: "31"}

# 5x5 block font. Only the nine letters the three words need.
_GLYPHS = {
    "P": ["#####",
          "#   #",
          "#####",
          "#    ",
          "#    "],
    "A": ["#####",
          "#   #",
          "#####",
          "#   #",
          "#   #"],
    "S": ["#####",
          "#    ",
          "#####",
          "    #",
          "#####"],
    "W": ["#   #",
          "#   #",
          "# # #",
          "## ##",
          "#   #"],
    "R": ["#####",
          "#   #",
          "#####",
          "#  # ",
          "#   #"],
    "N": ["#   #",
          "##  #",
          "# # #",
          "#  ##",
          "#   #"],
    "F": ["#####",
          "#    ",
          "#####",
          "#    ",
          "#    "],
    "I": ["#####",
          "  #  ",
          "  #  ",
          "  #  ",
          "#####"],
    "L": ["#    ",
          "#    ",
          "#    ",
          "#    ",
          "#####"],
}

RULE_WIDTH = 62


def _unicode_ok(stream):
    """Can this stream actually encode the block character?

    An offline workstation on a non-UTF-8 locale will raise on the first block
    character otherwise, turning a status banner into a traceback.
    """
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return False
    try:
        u"█─".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def render_word(word, block="#"):
    """Render one word as a list of text rows."""
    rows = []
    for index in range(5):
        parts = [_GLYPHS[letter][index] for letter in word if letter in _GLYPHS]
        rows.append("  ".join(parts).replace("#", block))
    return rows


def classify(status, verdict):
    """Return ``(result, notes)`` from the exit status and the risk verdict.

    ``verdict`` is the ``verdict`` block of a ``risk_<stage>.json``, or one of
    the sentinels ``None`` (no verdict expected) / ``"missing"`` (one was
    expected but could not be read).
    """
    notes = []

    if verdict == "missing":
        if status != 0:
            return FAIL, ["判定檔讀不到，且指令以狀態 {0} 結束".format(status)]
        return WARN, ["判定檔讀不到 —— 這一階段的風險**未經檢查**，不等於沒問題"]

    if isinstance(verdict, dict):
        counts = verdict.get("counts") or {}
        waived = verdict.get("waived") or 0

        if not verdict.get("bringup_ok", True):
            count = verdict.get("bringup_blocker_count", counts.get("BLOCKER", 0))
            note = "BLOCKER × {0} —— 不可上板".format(count)
            if status == 0:
                note += "（exit code 未擋下，需要 -fail-on-blocker 才會擋）"
            notes.append(note)
            if waived:
                notes.append("另有 {0} 項已豁免，不計入判定".format(waived))
            return FAIL, notes

        if status != 0:
            notes.append("指令以狀態 {0} 結束".format(status))
            return FAIL, notes

        if not verdict.get("signoff_ok", True):
            notes.append("CRITICAL × {0} —— 可上板驗證，但不可 sign-off".format(
                verdict.get("signoff_blocker_count", counts.get("CRITICAL", 0))))
            return WARN, notes

        if counts.get("WARNING"):
            notes.append("WARNING × {0}".format(counts["WARNING"]))
            return WARN, notes

        if waived:
            notes.append("{0} 項已豁免".format(waived))
        return PASS, notes

    if status != 0:
        return FAIL, ["指令以狀態 {0} 結束".format(status)]
    return PASS, notes


def load_verdict(path):
    """``None`` when no path was given, ``"missing"`` when it cannot be read."""
    if not path:
        return None
    if not os.path.isfile(path):
        return "missing"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return "missing"
    verdict = data.get("verdict")
    if not isinstance(verdict, dict):
        return "missing"
    return verdict


def render(result, label, notes, block="#", rule="-", colour=None):
    # The word also goes out as plain text. The art is for the human at the
    # terminal; a log scraper, a CI step or `grep` needs something matchable,
    # and block letters are not it.
    lines = [rule * RULE_WIDTH]
    lines.append("[{0}] {1}".format(result, label) if label
                 else "[{0}]".format(result))
    lines.append("")

    art = render_word(result, block)
    if colour:
        art = ["\033[1;{0}m{1}\033[0m".format(colour, row) for row in art]
    for row in art:
        lines.append("  " + row)

    if notes:
        lines.append("")
        for note in notes:
            lines.append("  " + note)
    lines.append(rule * RULE_WIDTH)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Large PASS/WARN/FAIL banner for the end of a make target.")
    parser.add_argument("--status", type=int, default=0,
                        help="exit status of the command that just ran")
    parser.add_argument("--label", default="", help="stage name to print above")
    parser.add_argument("--verdict",
                        help="risk_<stage>.json to grade on; when given but "
                             "unreadable the banner reports WARN, never PASS")
    parser.add_argument("--note", action="append", default=[],
                        help="extra line under the banner (repeatable)")
    parser.add_argument("--ascii", action="store_true",
                        help="force ASCII output")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args(argv)

    verdict = load_verdict(args.verdict)
    result, notes = classify(args.status, verdict)
    notes.extend(args.note)

    plain = args.ascii or os.environ.get("BANNER_ASCII") or not _unicode_ok(
        sys.stdout)
    block = "#" if plain else u"█"
    rule = "-" if plain else u"─"

    colour = None
    if not (args.no_color or os.environ.get("NO_COLOR")):
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            colour = _COLOURS[result]

    print(render(result, args.label, notes, block, rule, colour))

    # The banner reports; it never decides. Passing the status through keeps
    # `make check` failing fast and `make all` stopping where it should.
    return args.status


if __name__ == "__main__":
    sys.exit(main())
