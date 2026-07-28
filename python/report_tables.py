#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic parsers for the table layouts Vivado uses across its reports.

Vivado emits three shapes that the analysis needs:

1. Pipe-bordered tables (``report_utilization``, ``report_clock_interaction``)::

       +-------------+------+-------+
       |  Site Type  | Used | Util% |
       +-------------+------+-------+
       | Slice LUTs  | 1234 | 90.00 |
       +-------------+------+-------+

2. Ruler-aligned tables (the timing summary, some CDC output)::

       Severity   ID      Description
       ---------  ------  -----------
       Critical   CDC-1   1-bit unknown CDC circuitry

3. Violation blocks (``report_drc``, ``report_methodology``)::

       NSTD-1#1 Critical Warning
       Unspecified I/O Standard
       3 out of 5 logical ports use I/O standard ...
       Related violations: <none>

Column-boundary logic for style 2 is shared with ``vivado_report_parser`` rather
than duplicated. Every function here is total: malformed input yields an empty
result, never an exception, because a parse failure must degrade to "this was
not checked" rather than take down the whole run.

Standard library only, Python 3.4+.
"""

import re

from vivado_report_parser import column_spans, normalise_column


_BORDER = re.compile(r"^\s*\+[-+]+\+\s*$")
_PIPE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RULER = re.compile(r"^\s*-{2,}(?:\s+-{2,})+\s*$")

# e.g. "NSTD-1#1 Critical Warning" / "TIMING-6#3 Warning"
_VIOLATION_HEAD = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*-\d+)#(\d+)\s+"
    r"(Error|Critical Warning|Warning|Advisory)\s*$")

def _split_pipe_row(line):
    inner = line.strip()
    inner = inner[1:-1] if inner.startswith("|") and inner.endswith("|") else inner
    return [cell.strip() for cell in inner.split("|")]


def _rows_to_dicts(header, rows):
    """Zip header names onto row cells, tolerating ragged rows."""
    keys = [normalise_column(name) for name in header]
    records = []
    for row in rows:
        if len(row) < len(keys):
            row = row + [""] * (len(keys) - len(row))
        record = {}
        for index, key in enumerate(keys):
            if key:
                record[key] = row[index].strip()
        record["_cells"] = row[:len(keys)]
        records.append(record)
    return records


def parse_pipe_tables(text):
    """Return every pipe-bordered table as {title, columns, rows}."""
    lines = text.splitlines() if not isinstance(text, list) else text
    tables = []
    index = 0

    while index < len(lines):
        if not _BORDER.match(lines[index]):
            index += 1
            continue

        # Walk the contiguous block of border/row lines that forms one table.
        block = []
        start = index
        while index < len(lines) and (_BORDER.match(lines[index])
                                      or _PIPE_ROW.match(lines[index])):
            if _PIPE_ROW.match(lines[index]):
                block.append(_split_pipe_row(lines[index]))
            index += 1

        if len(block) >= 2:
            tables.append({
                "title": _preceding_title(lines, start),
                "columns": block[0],
                "rows": _rows_to_dicts(block[0], block[1:]),
                "line_start": start,
            })
        elif index == start:
            index += 1

    return tables


def _preceding_title(lines, index):
    """Nearest non-empty line above a table, used to tell tables apart."""
    scan = index - 1
    while scan >= 0 and scan > index - 8:
        candidate = lines[scan].strip()
        if candidate and not set(candidate) <= set("-+=| "):
            return candidate
        scan -= 1
    return ""


def parse_ruler_tables(text):
    """Return every ruler-aligned table as {title, columns, rows}."""
    lines = text.splitlines() if not isinstance(text, list) else text
    tables = []

    for index, line in enumerate(lines):
        if not _RULER.match(line) or index == 0:
            continue
        header_line = lines[index - 1]
        if not header_line.strip():
            continue

        spans = column_spans(line)
        if len(spans) < 2:
            continue
        header = [header_line[start:end].strip() for start, end in spans]
        if not any(header):
            continue

        rows = []
        for row_line in lines[index + 1:]:
            if not row_line.strip():
                break
            if _RULER.match(row_line) or _BORDER.match(row_line):
                break
            # The final column often overflows its ruler width, so it takes
            # everything to the end of the line.
            cells = []
            for position, (start, end) in enumerate(spans):
                if position == len(spans) - 1:
                    cells.append(row_line[start:].strip())
                else:
                    cells.append(row_line[start:end].strip())
            rows.append(cells)

        if rows:
            tables.append({
                "title": _preceding_title(lines, index - 1),
                "columns": header,
                "rows": _rows_to_dicts(header, rows),
                "line_start": index - 1,
            })

    return tables


def parse_all_tables(text):
    """Both table styles, in the order they appear."""
    tables = parse_pipe_tables(text) + parse_ruler_tables(text)
    tables.sort(key=lambda table: table["line_start"])
    return tables


def find_table(tables, required, title_hint=None):
    """First table whose columns cover ``required`` (normalised names).

    ``title_hint`` is a lowercase substring used to prefer one of several
    tables that share a column signature.
    """
    wanted = set(normalise_column(name) for name in required)
    matches = []
    for table in tables:
        columns = set(normalise_column(name) for name in table["columns"])
        if wanted <= columns:
            matches.append(table)

    if not matches:
        return None
    if title_hint:
        for table in matches:
            if title_hint.lower() in (table["title"] or "").lower():
                return table
    return matches[0]


def parse_violation_blocks(text):
    """Parse the ``RULE#n Severity`` blocks of report_drc / report_methodology.

    Returns a list of {rule, instance, severity, title, detail}.
    """
    lines = text.splitlines() if not isinstance(text, list) else text
    violations = []
    current = None
    body = []

    def close():
        if current is None:
            return
        text_body = [line.strip() for line in body if line.strip()]
        # First line after the header is the short rule title; the rest is the
        # explanatory detail Vivado prints underneath.
        current["title"] = text_body[0] if text_body else ""
        detail = [line for line in text_body[1:]
                  if not line.startswith("Related violations:")]
        current["detail"] = " ".join(detail)
        violations.append(current)

    for line in lines:
        head = _VIOLATION_HEAD.match(line)
        if head:
            close()
            current = {
                "rule": head.group(1),
                "instance": int(head.group(2)),
                "severity": head.group(3),
                "title": "",
                "detail": "",
            }
            body = []
            continue
        if current is not None:
            body.append(line)

    close()
    return violations


def to_float(value):
    """Parse a Vivado numeric cell, tolerating ``<0.01``, ``NA`` and ``%``."""
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text or text.upper() in ("NA", "N/A", "-", "--"):
        return None
    text = text.lstrip("<>~")
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value):
    number = to_float(value)
    return None if number is None else int(number)
