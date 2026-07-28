#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parsers for the non-timing Vivado reports feeding the risk assessment.

Every parser returns a dict carrying ``available`` and, when that is False, a
``reason``. This is deliberate and load-bearing: a risk report that renders an
unparseable CDC report as "no findings" would tell the user their clock domain
crossings are safe when they were never checked. Nothing here raises.

Standard library only, Python 3.4+.
"""

import os
import re

from report_tables import (find_table, parse_all_tables, parse_pipe_tables,
                           parse_violation_blocks, to_float, to_int)


def _unavailable(reason):
    return {"available": False, "reason": reason}


def _read(path):
    """Return file text, or None when it is missing or unreadable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except (IOError, OSError):
        return None


def _guard(path, parse):
    """Run ``parse`` on the file at ``path``, degrading instead of raising."""
    text = _read(path)
    if text is None:
        return _unavailable("report not generated or unreadable: {0}".format(
            path or "<not requested>"))
    if not text.strip():
        return _unavailable("report is empty: {0}".format(path))
    try:
        result = parse(text)
    except Exception as error:  # noqa: BLE001 - never break the flow on a report
        return _unavailable("could not parse {0}: {1}".format(
            os.path.basename(path), error))
    if result is None:
        return _unavailable(
            "unrecognised format in {0} (expected Vivado 2021.2 layout)".format(
                os.path.basename(path)))
    result["available"] = True
    result["source"] = path
    return result


# ---------------------------------------------------------------------------
# report_utilization
# ---------------------------------------------------------------------------

# Resource rows worth judging. Vivado names differ between 7-series and
# UltraScale(+), so both spellings map onto one canonical key.
_UTIL_INTEREST = {
    "slice luts": "lut",
    "clb luts": "lut",
    "slice registers": "register",
    "clb registers": "register",
    "register as flip flop": "register",
    "slice": "slice",
    "clb": "slice",
    "block ram tile": "bram",
    "dsps": "dsp",
    "bonded iob": "iob",
    "bufgctrl": "bufg",
    "global clock buffers": "bufg",
}


def _parse_utilization(text):
    tables = parse_pipe_tables(text)
    if not tables:
        return None

    resources = {}
    for table in tables:
        if not find_table([table], ["Site Type", "Used"]):
            continue
        for row in table["rows"]:
            name = (row.get("site_type") or "").strip()
            if not name:
                continue
            canonical = _UTIL_INTEREST.get(name.lower())
            if not canonical or canonical in resources:
                continue

            used = to_int(row.get("used"))
            available = to_int(row.get("available"))
            percent = to_float(row.get("util"))
            if percent is None and used is not None and available:
                percent = 100.0 * used / available
            if used is None or percent is None:
                continue

            resources[canonical] = {
                "name": name,
                "used": used,
                "available": available,
                "util_percent": percent,
            }

    if not resources:
        return None
    worst = max(resources.values(), key=lambda item: item["util_percent"])
    return {"resources": resources, "worst": worst}


def parse_utilization(path):
    return _guard(path, _parse_utilization)


# ---------------------------------------------------------------------------
# report_drc / report_methodology (identical violation-block layout)
# ---------------------------------------------------------------------------

_VIOLATIONS_FOUND = re.compile(r"^\s*Violations found:\s*(\d+)", re.MULTILINE)


def _parse_rule_report(text):
    violations = parse_violation_blocks(text)

    declared = _VIOLATIONS_FOUND.search(text)
    declared_count = int(declared.group(1)) if declared else None

    if not violations:
        # "Violations found: 0" is a genuine clean result; no marker at all
        # means the layout was not what we expected.
        if declared_count == 0:
            return {"violations": [], "declared_count": 0, "by_severity": {}}
        return None

    by_severity = {}
    for violation in violations:
        severity = violation["severity"]
        by_severity[severity] = by_severity.get(severity, 0) + 1

    return {
        "violations": violations,
        "declared_count": declared_count,
        "by_severity": by_severity,
    }


def parse_drc(path):
    return _guard(path, _parse_rule_report)


def parse_methodology(path):
    return _guard(path, _parse_rule_report)


# ---------------------------------------------------------------------------
# report_cdc
# ---------------------------------------------------------------------------

def _parse_cdc(text):
    tables = parse_all_tables(text)

    detail_table = (find_table(tables, ["Severity", "ID", "Description"])
                    or find_table(tables, ["Severity", "ID"]))
    summary_table = find_table(tables, ["Severity", "Count"])

    if detail_table is None and summary_table is None:
        return None

    findings = []
    if detail_table is not None:
        for row in detail_table["rows"]:
            severity = (row.get("severity") or "").strip()
            identifier = (row.get("id") or "").strip()
            if not severity or not identifier:
                continue
            findings.append({
                "severity": severity,
                "id": identifier,
                "description": (row.get("description") or "").strip(),
                "source_clock": (row.get("source_clock") or "").strip(),
                "destination_clock": (row.get("destination_clock") or "").strip(),
                "endpoints": to_int(row.get("endpoints")),
            })

    by_severity = {}
    if summary_table is not None:
        for row in summary_table["rows"]:
            severity = (row.get("severity") or "").strip()
            count = to_int(row.get("count"))
            if severity and count is not None:
                by_severity[severity] = count
    if not by_severity:
        for finding in findings:
            by_severity[finding["severity"]] = by_severity.get(
                finding["severity"], 0) + 1

    return {"findings": findings, "by_severity": by_severity}


def parse_cdc(path):
    return _guard(path, _parse_cdc)


# ---------------------------------------------------------------------------
# report_clock_interaction
# ---------------------------------------------------------------------------

def _parse_clock_interaction(text):
    tables = parse_all_tables(text)
    table = (find_table(tables, ["Source Clock", "Destination Clock"]))
    if table is None:
        return None

    # The classification column has been spelled several ways across versions.
    classification_key = None
    for candidate in ("clock_pair_classification", "clock_pair_class",
                      "path_group_classification", "classification"):
        if any(candidate in row for row in table["rows"]):
            classification_key = candidate
            break

    pairs = []
    for row in table["rows"]:
        source = (row.get("source_clock") or "").strip()
        destination = (row.get("destination_clock") or "").strip()
        if not source or not destination:
            continue
        pairs.append({
            "source_clock": source,
            "destination_clock": destination,
            "classification": (row.get(classification_key) or "").strip()
            if classification_key else "",
            "common_primary_clock": (row.get("common_primary_clock") or "").strip(),
            "inter_clock_constraints":
                (row.get("inter_clock_constraints") or "").strip(),
        })

    if not pairs:
        return None
    return {"pairs": pairs, "has_classification": bool(classification_key)}


def parse_clock_interaction(path):
    return _guard(path, _parse_clock_interaction)


# ---------------------------------------------------------------------------
# report_control_sets
# ---------------------------------------------------------------------------

_UNIQUE_CONTROL_SETS = re.compile(
    r"(?:number of unique control sets|unique control sets)\D*(\d+)",
    re.IGNORECASE)


def _parse_control_sets(text):
    match = _UNIQUE_CONTROL_SETS.search(text)
    if match:
        return {"unique_control_sets": int(match.group(1))}

    # Some versions only present it as a row in a pipe table.
    for table in parse_pipe_tables(text):
        for row in table["rows"]:
            cells = row.get("_cells") or []
            for index, cell in enumerate(cells[:-1]):
                if "unique control set" in cell.lower():
                    count = to_int(cells[index + 1])
                    if count is not None:
                        return {"unique_control_sets": count}
    return None


def parse_control_sets(path):
    return _guard(path, _parse_control_sets)


# ---------------------------------------------------------------------------

PARSERS = {
    "utilization": parse_utilization,
    "drc": parse_drc,
    "methodology": parse_methodology,
    "cdc": parse_cdc,
    "clock_interaction": parse_clock_interaction,
    "control_sets": parse_control_sets,
}

# Human-facing names, used when telling the user what was not checked.
REPORT_LABELS = {
    "utilization": "Utilization（資源使用率）",
    "drc": "DRC（設計規則檢查）",
    "methodology": "Methodology（方法學檢查）",
    "cdc": "CDC（跨時脈域）",
    "clock_interaction": "Clock Interaction（時脈交互作用）",
    "control_sets": "Control Sets",
}


def parse_reports(paths):
    """Parse whichever of the known reports were supplied.

    ``paths`` maps report kind -> file path. Kinds with no path still appear in
    the result as unavailable, so the risk engine can say they were not checked.
    """
    results = {}
    for kind, parser in PARSERS.items():
        results[kind] = parser((paths or {}).get(kind))
    return results
