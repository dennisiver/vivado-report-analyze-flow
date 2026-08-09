#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check this flow's parsers against the reports your Vivado actually produced.

The parsers were written against the documented Vivado 2021.2 layouts. Real
projects can differ, so this tool runs every parser over a directory of real
reports and prints what each one extracted, letting you compare against the
``.rpt`` yourself without sending the design anywhere.

    python3 python/check_reports.py --dir timing_analysis/raw

For anything that fails to parse it prints a structural fingerprint -- banner
keys, section titles, table borders and column headers -- with hierarchical
instance names masked, so the layout can be diagnosed without exposing the
design. Read it before sharing it; masking is deliberately conservative but it
is not a guarantee.

Standard library only, Python 3.4+.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import risk_rules as risk_mod             # noqa: E402
import vivado_report_parser as timing_mod  # noqa: E402
import vivado_reports as reports_mod      # noqa: E402


FINGERPRINT_LINES = 40

# Structural lines worth showing: they describe layout, not design content.
_KEEP = [
    re.compile(r"^\s*\+[-+]+\+\s*$"),                 # pipe table border
    re.compile(r"^\s*\|.*\|\s*$"),                    # pipe table row
    re.compile(r"^\s*-{2,}(\s+-{2,})+\s*$"),          # ruler
    re.compile(r"^\s*\d+(\.\d+)*\.\s+\S"),            # numbered section title
    re.compile(r"^\s*Violations found:"),             # rule report count
    re.compile(r"^[A-Z][A-Z0-9_]*-\d+#\d+\s+"),       # violation head
    re.compile(r"^\s*(Severity|Rule|Site Type|Source Clock|Clock)\b"),
]

# Tokens that look like instance/net paths or file paths get masked.
_PATHY = re.compile(r"\S*[/\\]\S*")
_BRACKETED = re.compile(r"\S+\[\d+\]\S*")


def _mask(line):
    line = _PATHY.sub("<name>", line)
    line = _BRACKETED.sub("<name>", line)
    return line


def _fingerprint(path, limit=FINGERPRINT_LINES):
    """Structure-only excerpt of a report we could not parse."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except (IOError, OSError) as error:
        return ["<could not read: {0}>".format(error)]

    kept = []
    for line in lines:
        if any(pattern.match(line) for pattern in _KEEP):
            kept.append(_mask(line.rstrip())[:160])
            if len(kept) >= limit:
                kept.append("... (truncated)")
                break

    if not kept:
        kept = ["<no recognisable table or violation structure found>"]

    # Always show the tool banner: it confirms the Vivado version.
    banner = []
    for line in lines[:20]:
        match = re.match(r"^\|\s*(Tool Version|Design State)\s*:\s*(.*)$", line)
        if match:
            banner.append("| {0} : {1}".format(match.group(1),
                                               match.group(2).strip()))
    return banner + kept


def _describe(kind, result):
    """One-line-per-fact summary of what a parser pulled out."""
    facts = []
    if kind == "utilization":
        for key, resource in sorted((result.get("resources") or {}).items()):
            facts.append("{0:<10} {1:<22} {2}/{3} = {4:.2f}%".format(
                key, resource["name"], resource["used"],
                resource["available"], resource["util_percent"]))
    elif kind in ("drc", "methodology"):
        facts.append("declared count: {0}".format(result.get("declared_count")))
        for violation in result.get("violations") or []:
            facts.append("{0:<14} {1:<18} {2}".format(
                violation["rule"], violation["severity"],
                violation["title"][:60]))
    elif kind == "cdc":
        facts.append("by severity: {0}".format(result.get("by_severity")))
        for item in (result.get("findings") or [])[:15]:
            facts.append("{0:<10} {1:<10} {2} -> {3}".format(
                item["severity"], item["id"],
                item["source_clock"] or "?", item["destination_clock"] or "?"))
    elif kind == "clock_interaction":
        for pair in (result.get("pairs") or [])[:15]:
            facts.append("{0} -> {1}: {2}".format(
                pair["source_clock"], pair["destination_clock"],
                pair["classification"] or "<no classification column>"))
    elif kind == "control_sets":
        facts.append("unique control sets: {0}".format(
            result.get("unique_control_sets")))
    return facts


def _check_timing(path, verbose):
    print("=" * 72)
    print("timing_summary")
    print("=" * 72)
    if not path or not os.path.isfile(path):
        print("  MISSING   {0}".format(path or "<not found>"))
        return None

    try:
        parsed = timing_mod.parse_timing_summary_file(path)
    except Exception as error:  # noqa: BLE001
        print("  FAILED    {0}".format(error))
        return None

    summary = parsed.get("summary") or {}
    paths = parsed.get("paths") or []
    checks = (parsed.get("check_timing") or {}).get("items") or {}

    ok = bool(summary) and summary.get("wns") is not None
    print("  {0}  {1}".format("OK      " if ok else "SUSPECT ", path))
    print("  design={0} device={1} state={2}".format(
        (parsed.get("meta") or {}).get("design"),
        (parsed.get("meta") or {}).get("device"),
        (parsed.get("meta") or {}).get("design_state")))
    print("  WNS={0} TNS={1} failing={2}/{3}".format(
        summary.get("wns"), summary.get("tns"),
        summary.get("tns_failing_endpoints"),
        summary.get("tns_total_endpoints")))
    print("  WHS={0} THS={1} WPWS={2}".format(
        summary.get("whs"), summary.get("ths"), summary.get("wpws")))
    print("  clocks={0} paths_parsed={1} check_timing_items={2}".format(
        len(parsed.get("clocks") or []), len(paths), len(checks)))

    if not ok:
        print("\n  --- structural fingerprint ---")
        for line in _fingerprint(path):
            print("  " + line)
    elif verbose:
        for path_item in timing_mod.worst_paths(paths, limit=3):
            print("    slack={0} group={1} levels={2}".format(
                path_item["slack_ns"], path_item["path_group"],
                path_item["logic_levels"]))
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify the parsers against your own Vivado reports.")
    parser.add_argument("--dir", required=True,
                        help="directory holding the .rpt files (usually "
                             "timing_analysis/raw)")
    parser.add_argument("--stage", default="impl_1",
                        help="run name used in the filenames (default: impl_1)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.dir):
        sys.stderr.write("error: not a directory: {0}\n".format(args.dir))
        return 2

    def locate(stem):
        """Find <stem>_<stage>.rpt, falling back to any <stem>*.rpt."""
        exact = os.path.join(args.dir, "{0}_{1}.rpt".format(stem, args.stage))
        if os.path.isfile(exact):
            return exact
        for name in sorted(os.listdir(args.dir)):
            if name.startswith(stem) and name.endswith(".rpt"):
                return os.path.join(args.dir, name)
        return None

    timing = _check_timing(locate("timing_summary"), args.verbose)

    failures = []
    report_paths = {}
    for kind in sorted(reports_mod.PARSERS):
        path = locate(kind)
        report_paths[kind] = path
        result = reports_mod.PARSERS[kind](path)

        print("")
        print("=" * 72)
        print(kind)
        print("=" * 72)
        if result.get("available"):
            print("  OK        {0}".format(path))
            for fact in _describe(kind, result):
                print("    " + fact)
        else:
            failures.append(kind)
            print("  FAILED    {0}".format(result.get("reason")))
            if path and os.path.isfile(path):
                print("\n  --- structural fingerprint (instance names masked) ---")
                for line in _fingerprint(path):
                    print("  " + line)

    print("")
    print("=" * 72)
    print("risk assessment")
    print("=" * 72)
    reports = reports_mod.parse_reports(report_paths)
    assessment = risk_mod.evaluate(
        timing=timing, reports=reports,
        requested_reports=set(k for k, v in report_paths.items() if v))
    verdict = assessment["verdict"]
    print("  bring-up: {0}   sign-off: {1}".format(
        "OK" if verdict["bringup_ok"] else "BLOCKED",
        "OK" if verdict["signoff_ok"] else "BLOCKED"))
    print("  counts: {0}".format(verdict["counts"]))
    for item in assessment["findings"]:
        print("    [{0:<8}] {1:<28} {2}".format(
            item["severity"], item["id"], item["title"][:70]))

    print("")
    print("=" * 72)
    if failures:
        print("{0} report(s) could not be parsed: {1}".format(
            len(failures), ", ".join(failures)))
        print("Send the fingerprint block(s) above so the parser can be "
              "adjusted. Check them over first -- instance names are masked, "
              "but review before sharing.")
    else:
        print("All reports parsed. Compare the extracted values above against "
              "the .rpt files to confirm they are correct.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
