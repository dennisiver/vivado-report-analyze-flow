#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract the messages that matter from Vivado's logs.

Some of the most damaging things Vivado does are announced only in the log and
appear in no report at all. The worst of them is a constraint that parsed fine
but matched nothing::

    WARNING: [Vivado 12-507] No objects matched 'get_ports clk_in'

The XDC was in the fileset and was read; one line of it simply did nothing, and
the timing report that follows is computed against constraints you do not
actually have. ``check_timing`` sees the consequence (endpoints nobody
constrained); only the log names the constraint.

Two design points drive this module:

1. **Severity alone is not enough.** That message, and ``inferring latch``, are
   plain WARNINGs. Filtering on CRITICAL WARNING would discard exactly the ones
   worth having. So there is a curated table, matched on message ID *and* on
   message text -- text is the durable half, because message IDs move between
   Vivado releases.

2. **Aggregate, never enumerate.** A synthesis log runs to tens of thousands of
   lines and repeats a message per instance. Results are grouped by message ID
   with a count and a few examples, keeping this as cheap as every other part
   of the flow.

Standard library only, Python 3.4+.
"""

import os
import re


# "CRITICAL WARNING: [Vivado 12-507] No objects matched ..."
_MESSAGE = re.compile(
    r"^\s*(INFO|WARNING|CRITICAL WARNING|ERROR)\s*:\s*"
    r"\[([A-Za-z][\w-]*\s+\d+-\d+)\]\s*(.*)$")

SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL WARNING": 2, "ERROR": 3}

# Curated classes of message that matter more than their severity suggests.
# `ids` are best-effort for Vivado 2024.2; `patterns` are the durable match and
# are what the tests exercise. Extend either list as real logs come in.
HIGH_RISK_MESSAGES = [
    {
        "key": "CONSTRAINT_NOT_APPLIED",
        "ids": ["Vivado 12-507", "Vivado 12-1008", "Common 17-55",
                "Common 17-69", "Vivado 12-4739"],
        "patterns": ["no objects matched", "expects at least one object",
                     "no constraint will be written",
                     "cannot be applied to any object"],
        "label": "Constraint 沒有套用到任何物件",
    },
    {
        "key": "UNBOUND_MODULE",
        "ids": ["Synth 8-448", "Synth 8-3491", "Netlist 29-160"],
        "patterns": ["black box", "unable to bind", "cannot find module",
                     "module not found", "unresolved"],
        "label": "模組沒有接上（黑盒子／找不到定義）",
    },
    {
        "key": "INFERRED_LATCH",
        "ids": ["Synth 8-327"],
        "patterns": ["inferring latch"],
        "label": "推論出非預期的 latch",
    },
    {
        "key": "UNDRIVEN_NET",
        "ids": ["Synth 8-3848", "Synth 8-3332"],
        "patterns": ["does not have driver", "has no driver",
                     "multiple drivers", "multi-driven"],
        "label": "訊號未驅動或多重驅動",
    },
    {
        "key": "WIDTH_MISMATCH",
        "ids": ["Synth 8-6014", "Synth 8-693"],
        "patterns": ["truncated", "width mismatch", "size mismatch"],
        "label": "位寬不匹配或被截斷",
    },
    {
        "key": "PLACEMENT_QUALITY",
        "ids": ["Place 30-575", "Route 35-459"],
        "patterns": ["poor placement", "congestion", "overlapping"],
        "label": "佈局或繞線品質不佳",
    },
]

MAX_EXAMPLES = 3


def classify(message_id, text):
    """Return the curated class key for a message, or None."""
    lowered = (text or "").lower()
    identifier = (message_id or "").strip()
    for entry in HIGH_RISK_MESSAGES:
        if identifier in entry["ids"]:
            return entry["key"]
        for pattern in entry["patterns"]:
            if pattern in lowered:
                return entry["key"]
    return None


def class_label(key):
    for entry in HIGH_RISK_MESSAGES:
        if entry["key"] == key:
            return entry["label"]
    return key


def parse_log_text(text):
    """Aggregate one log's messages by message ID."""
    grouped = {}
    counts = {"INFO": 0, "WARNING": 0, "CRITICAL WARNING": 0, "ERROR": 0}

    for line in text.splitlines():
        match = _MESSAGE.match(line)
        if not match:
            continue
        severity, identifier, body = match.group(1), match.group(2), match.group(3)
        # Vivado writes the id with variable internal spacing.
        identifier = re.sub(r"\s+", " ", identifier).strip()
        counts[severity] = counts.get(severity, 0) + 1

        entry = grouped.get(identifier)
        if entry is None:
            entry = {
                "id": identifier,
                "severity": severity,
                "count": 0,
                "examples": [],
                "risk_class": classify(identifier, body),
            }
            grouped[identifier] = entry

        entry["count"] += 1
        # Keep the worst severity seen for an id; Vivado can vary it by context.
        if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(entry["severity"], 0):
            entry["severity"] = severity
        if len(entry["examples"]) < MAX_EXAMPLES:
            entry["examples"].append(body.strip()[:300])
        if entry["risk_class"] is None:
            entry["risk_class"] = classify(identifier, body)

    return {
        "messages": sorted(grouped.values(),
                           key=lambda item: (-SEVERITY_RANK.get(item["severity"], 0),
                                             -item["count"], item["id"])),
        "counts": counts,
    }


def parse_log(path):
    """Parse one log file, degrading to unavailable rather than raising."""
    if not path or not os.path.isfile(path):
        return {"available": False,
                "reason": "log not found: {0}".format(path or "<not given>"),
                "path": path}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except (IOError, OSError) as error:
        return {"available": False,
                "reason": "cannot read log: {0}".format(error), "path": path}

    try:
        result = parse_log_text(text)
    except Exception as error:  # noqa: BLE001 - a log must never break the flow
        return {"available": False,
                "reason": "could not parse log: {0}".format(error), "path": path}

    result["available"] = True
    result["path"] = path
    return result


def parse_logs(paths):
    """Parse several logs and merge them into one aggregate."""
    merged = {}
    counts = {"INFO": 0, "WARNING": 0, "CRITICAL WARNING": 0, "ERROR": 0}
    sources = []
    unavailable = []

    for path in paths or []:
        parsed = parse_log(path)
        if not parsed.get("available"):
            unavailable.append(parsed)
            continue
        sources.append(path)
        for severity, value in parsed["counts"].items():
            counts[severity] = counts.get(severity, 0) + value
        for message in parsed["messages"]:
            entry = merged.get(message["id"])
            if entry is None:
                merged[message["id"]] = dict(message)
                continue
            entry["count"] += message["count"]
            if (SEVERITY_RANK.get(message["severity"], 0)
                    > SEVERITY_RANK.get(entry["severity"], 0)):
                entry["severity"] = message["severity"]
            for example in message["examples"]:
                if len(entry["examples"]) < MAX_EXAMPLES:
                    entry["examples"].append(example)

    return {
        "available": bool(sources),
        "reason": "" if sources else "no readable log files",
        "sources": sources,
        "unavailable": unavailable,
        "counts": counts,
        "messages": sorted(merged.values(),
                           key=lambda item: (-SEVERITY_RANK.get(item["severity"], 0),
                                             -item["count"], item["id"])),
    }


def by_risk_class(parsed):
    """Group the curated findings by class, worst severity first."""
    grouped = {}
    for message in parsed.get("messages") or []:
        key = message.get("risk_class")
        if not key:
            continue
        entry = grouped.setdefault(key, {
            "key": key, "label": class_label(key),
            "count": 0, "ids": [], "severity": "INFO", "examples": [],
        })
        entry["count"] += message["count"]
        entry["ids"].append(message["id"])
        if (SEVERITY_RANK.get(message["severity"], 0)
                > SEVERITY_RANK.get(entry["severity"], 0)):
            entry["severity"] = message["severity"]
        for example in message["examples"]:
            if len(entry["examples"]) < MAX_EXAMPLES:
                entry["examples"].append(example)
    return grouped
