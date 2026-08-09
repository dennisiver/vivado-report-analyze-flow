#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parse a Vivado ``report_timing_summary`` text report into a compact dict.

Standard library only, written for Python 3.4+ so it runs on an offline
workstation with either a system ``python3`` or the interpreter bundled with
Vivado (``$XILINX_VIVADO/tps/lnx64/python-3*/bin/python3``). The workstation OS
is not assumed; it is detected and recorded at run time by environment.py.

The point of this module is data reduction: a routed ``report_timing_summary``
is routinely tens of thousands of lines, and feeding it to a local LLM blows up
the context window.  Everything here throws away the bulk and keeps only the
numbers and the handful of worst paths that actually drive decisions.  Full
detail for a single path stays reachable through the recorded line ranges, so
a drill-down never requires re-reading the whole report.
"""

import re


NUM = r"[-+]?\d+(?:\.\d+)?"

_RE_HEADER = re.compile(r"^\|\s*([A-Za-z][A-Za-z ]*?)\s*:\s*(.*?)\s*$")
_RE_SECTION = re.compile(r"^\|\s*(.+?)\s*$")
_RE_CHECK_ITEM = re.compile(r"^\s*\d+\.\s+checking\s+(\S+)\s+\((\d+)\)\s*$")
_RE_CHECK_SECTION = re.compile(r"^\|\s*Check Timing\s*\((\d+)\)\s*$")
_RE_CLOCK_ROW = re.compile(
    r"^(\S+)\s+\{([^}]*)\}\s+(" + NUM + r")\s+(" + NUM + r")\s*$")

_RE_SLACK = re.compile(r"^Slack\s*\((VIOLATED|MET)\)\s*:\s*(" + NUM + r")ns")
_RE_FIELD = re.compile(r"^\s{2,}([A-Z][A-Za-z ]*?):\s*(.*?)\s*$")
_RE_CLOCKED_BY = re.compile(r"clocked by\s+(\S+)")
_RE_DATA_PATH = re.compile(
    r"^\s*(" + NUM + r")ns\s*\(logic\s+(" + NUM + r")ns\s*\((" + NUM + r")%\)"
    r"\s+route\s+(" + NUM + r")ns\s*\((" + NUM + r")%\)\)")
_RE_LOGIC_LEVELS = re.compile(r"^\s*(\d+)\s*(?:\((.*)\))?")
_RE_LEADING_NS = re.compile(r"^\s*(" + NUM + r")ns")

# Keys of the Design Timing Summary table, normalised from their column names.
_SUMMARY_INT_KEYS = frozenset([
    "tns_failing_endpoints", "tns_total_endpoints",
    "ths_failing_endpoints", "ths_total_endpoints",
    "tpws_failing_endpoints", "tpws_total_endpoints",
])


def _to_float(text):
    """Return ``text`` as a float, or None for Vivado's ``NA`` placeholders."""
    if text is None:
        return None
    text = text.strip()
    if not text or text.upper() in ("NA", "N/A", "INF", "-INF"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(text):
    value = _to_float(text)
    return None if value is None else int(value)


def normalise_column(name):
    """``TNS Failing Endpoints`` -> ``tns_failing_endpoints``."""
    name = name.replace("(ns)", "").replace("(MHz)", "").strip()
    name = re.sub(r"[^0-9A-Za-z]+", "_", name)
    return name.strip("_").lower()


def column_spans(dash_line):
    """Return (start, end) offsets for each run of dashes in a ruler line."""
    return [(m.start(), m.end()) for m in re.finditer(r"-{2,}", dash_line)]


# Shared with report_tables.py, which parses the same two table styles in the
# other Vivado reports.
_normalise_column = normalise_column
_column_spans = column_spans


def _parse_meta(lines):
    """Pull the ``| Tool Version : ...`` banner at the top of every report."""
    meta = {}
    for line in lines[:40]:
        match = _RE_HEADER.match(line)
        if match:
            meta[_normalise_column(match.group(1))] = match.group(2)
    return meta


def _parse_design_summary(lines):
    """Parse the Design Timing Summary table (WNS/TNS/WHS/THS/WPWS/TPWS)."""
    for index, line in enumerate(lines):
        if "WNS(ns)" not in line:
            continue
        # Expected shape: header line, ruler of dashes, then a single data row.
        dash_index = index + 1
        while dash_index < len(lines) and not lines[dash_index].strip():
            dash_index += 1
        if dash_index >= len(lines) or "---" not in lines[dash_index]:
            continue
        spans = _column_spans(lines[dash_index])
        if not spans:
            continue
        names = [_normalise_column(line[start:end]) for start, end in spans]

        data_index = dash_index + 1
        while data_index < len(lines) and not lines[data_index].strip():
            data_index += 1
        if data_index >= len(lines):
            continue
        values = lines[data_index].split()
        if len(values) != len(names):
            # Fall back to slicing by the ruler when a value ran into its
            # neighbour (very wide endpoint counts do this occasionally).
            values = [lines[data_index][start:end].strip() for start, end in spans]

        summary = {}
        for name, value in zip(names, values):
            if name in _SUMMARY_INT_KEYS:
                summary[name] = _to_int(value)
            else:
                summary[name] = _to_float(value)
        return summary
    return {}


def _parse_check_timing(lines):
    """Parse the ``check_timing`` table of contents.

    This is the single most useful section for catching constraints that never
    made it into the design: a spike in ``unconstrained_internal_endpoints`` or
    ``no_clock`` almost always means an XDC was not read.
    """
    result = {"total": None, "items": {}}
    for index, line in enumerate(lines):
        match = _RE_CHECK_SECTION.match(line)
        if match:
            result["total"] = int(match.group(1))
            continue
        match = _RE_CHECK_ITEM.match(line)
        if match:
            name, count = match.group(1), int(match.group(2))
            # The list appears twice (contents then body); keep the larger.
            if count >= result["items"].get(name, 0):
                result["items"][name] = count
    return result


def _parse_clocks(lines):
    """Parse the Clock Summary table.

    Sections are delimited by ``| <title>`` banner lines, so the table is
    everything between ``| Clock Summary`` and the next banner.
    """
    clocks = []
    in_section = False
    for line in lines:
        section = _RE_SECTION.match(line)
        if section:
            title = section.group(1).strip().lower()
            if title.startswith("-"):
                continue  # the "| -----" underline of a banner
            if in_section:
                break  # next section reached
            in_section = title.startswith("clock summary")
            continue
        if not in_section:
            continue
        match = _RE_CLOCK_ROW.match(line.strip())
        if match:
            clocks.append({
                "name": match.group(1),
                "waveform": match.group(2).strip(),
                "period_ns": _to_float(match.group(3)),
                "frequency_mhz": _to_float(match.group(4)),
            })
    return clocks


def _finish_path(path, fields):
    """Convert the raw ``Field: value`` map of one path block into typed data."""
    path["path_group"] = fields.get("Path Group")
    path["path_type"] = fields.get("Path Type")

    requirement = fields.get("Requirement")
    if requirement:
        match = _RE_LEADING_NS.match(requirement)
        path["requirement_ns"] = _to_float(match.group(1)) if match else None

    data_path = fields.get("Data Path Delay")
    if data_path:
        match = _RE_DATA_PATH.match(data_path)
        if match:
            path["data_path_delay_ns"] = _to_float(match.group(1))
            path["logic_delay_ns"] = _to_float(match.group(2))
            path["logic_pct"] = _to_float(match.group(3))
            path["route_delay_ns"] = _to_float(match.group(4))
            path["route_pct"] = _to_float(match.group(5))
        else:
            match = _RE_LEADING_NS.match(data_path)
            if match:
                path["data_path_delay_ns"] = _to_float(match.group(1))

    levels = fields.get("Logic Levels")
    if levels:
        match = _RE_LOGIC_LEVELS.match(levels)
        if match:
            path["logic_levels"] = _to_int(match.group(1))
            path["logic_levels_detail"] = (match.group(2) or "").strip() or None

    for field, key in (("Clock Path Skew", "clock_skew_ns"),
                       ("Clock Uncertainty", "clock_uncertainty_ns")):
        value = fields.get(field)
        if value:
            match = _RE_LEADING_NS.match(value)
            if match:
                path[key] = _to_float(match.group(1))
    return path


def _parse_paths(lines, max_paths=None):
    """Parse every ``Slack (...)`` block into a compact record.

    Each record keeps ``line_start``/``line_end`` so the full untruncated block
    can be re-read later without parsing (or loading) the whole report again.
    """
    paths = []
    delay_kind = None
    current = None
    fields = None
    pending_field = None

    def close(end_line):
        if current is not None:
            current["line_end"] = end_line
            paths.append(_finish_path(current, fields))

    for index, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("Max Delay Paths"):
            delay_kind = "max"
        elif stripped.startswith("Min Delay Paths"):
            delay_kind = "min"

        match = _RE_SLACK.match(stripped)
        if match:
            close(index - 1)
            current = {
                "status": match.group(1),
                "slack_ns": _to_float(match.group(2)),
                "delay_kind": delay_kind,
                "line_start": index,
                "line_end": None,
                "source": None,
                "source_clock": None,
                "destination": None,
                "destination_clock": None,
                "path_group": None,
                "path_type": None,
                "requirement_ns": None,
                "data_path_delay_ns": None,
                "logic_delay_ns": None,
                "logic_pct": None,
                "route_delay_ns": None,
                "route_pct": None,
                "logic_levels": None,
                "logic_levels_detail": None,
                "clock_skew_ns": None,
                "clock_uncertainty_ns": None,
            }
            fields = {}
            pending_field = None
            continue

        if current is None:
            continue

        # The per-pin delay table marks the end of the interesting header part.
        if stripped.startswith("Location") and "Delay type" in stripped:
            pending_field = None
            continue

        field_match = _RE_FIELD.match(line)
        if field_match:
            name, value = field_match.group(1).strip(), field_match.group(2)
            fields[name] = value
            if name == "Source":
                current["source"] = value
                pending_field = "source"
            elif name == "Destination":
                current["destination"] = value
                pending_field = "destination"
            else:
                pending_field = None
            continue

        # The line after Source/Destination carries the launching clock:
        #   (rising edge-triggered cell FDRE clocked by clk_100 {...})
        if pending_field and "clocked by" in line:
            clock_match = _RE_CLOCKED_BY.search(line)
            if clock_match:
                current[pending_field + "_clock"] = clock_match.group(1)
            pending_field = None

    close(len(lines) - 1)

    if max_paths is not None:
        paths = paths[:max_paths]
    return paths


def _parse_constraints_met(lines):
    """Return True/False/None for the overall pass-fail verdict line."""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("All user specified timing constraints are met"):
            return True
        if stripped.startswith("Timing constraints are not met"):
            return False
    return None


def parse_timing_summary(text, max_paths=None):
    """Parse a ``report_timing_summary`` report into a dict.

    ``max_paths`` caps how many path blocks are retained; the report itself is
    normally already limited via ``report_timing_summary -max_paths``.
    """
    lines = text.splitlines()
    return {
        "meta": _parse_meta(lines),
        "summary": _parse_design_summary(lines),
        "constraints_met": _parse_constraints_met(lines),
        "check_timing": _parse_check_timing(lines),
        "clocks": _parse_clocks(lines),
        "paths": _parse_paths(lines, max_paths=max_paths),
        "total_lines": len(lines),
    }


def parse_timing_summary_file(path, max_paths=None):
    """Parse a report from disk, tolerating the odd non-UTF-8 byte."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        result = parse_timing_summary(handle.read(), max_paths=max_paths)
    result["source_report"] = path
    return result


def extract_path_block(report_path, line_start, line_end):
    """Return the raw report text for one path block, for drill-down output."""
    with open(report_path, "r", encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
    line_start = max(0, line_start)
    line_end = min(len(lines) - 1, line_end)
    return "\n".join(lines[line_start:line_end + 1])


def worst_paths(paths, limit=10, delay_kind="max"):
    """Return the ``limit`` worst paths, worst slack first.

    ``report_timing_summary`` sorts within each clock group but not globally,
    so a design with several clocks needs an explicit sort to surface the
    genuinely worst offenders.
    """
    selected = [p for p in paths
                if delay_kind is None or p.get("delay_kind") == delay_kind]
    selected = [p for p in selected if p.get("slack_ns") is not None]
    selected.sort(key=lambda p: p["slack_ns"])
    return selected[:limit]
