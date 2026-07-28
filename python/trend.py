#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Track timing metrics across Vivado runs via a small append-only history.

``history.jsonl`` holds one compact JSON object per run.  Keeping the trend in
its own tiny file means "is this better or worse than last time?" never
requires re-reading an archived report.

Standard library only, Python 3.4+.
"""

import json
import os


HISTORY_FILENAME = "history.jsonl"

# Metrics where a larger number is better (slack); everything else in the
# summary is a violation count or a total, where smaller is better.
_HIGHER_IS_BETTER = frozenset(["wns", "whs", "wpws", "tns", "ths", "tpws"])


def history_path(outdir):
    return os.path.join(outdir, HISTORY_FILENAME)


def load_history(outdir, stage=None, limit=None):
    """Read history records, oldest first, optionally filtered by stage."""
    path = history_path(outdir)
    if not os.path.isfile(path):
        return []

    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # skip a truncated line rather than lose the history
            if stage is None or record.get("stage") == stage:
                records.append(record)

    if limit is not None:
        records = records[-limit:]
    return records


def append_history(outdir, record):
    """Append one run record to the history file."""
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    with open(history_path(outdir), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")


def make_record(stage, timestamp, summary, manifest=None, extra=None,
                verdict=None):
    """Build the compact per-run record stored in ``history.jsonl``."""
    record = {"stage": stage, "timestamp": timestamp}
    for key in ("wns", "tns", "tns_failing_endpoints", "tns_total_endpoints",
                "whs", "ths", "ths_failing_endpoints",
                "wpws", "tpws", "tpws_failing_endpoints"):
        record[key] = summary.get(key)

    if manifest:
        git = manifest.get("git") or {}
        record["rtl_git_commit"] = git.get("short")
        record["rtl_git_dirty"] = git.get("dirty")
        record["sources_digest"] = manifest.get("digest", {}).get("sources", "")[:12]
        record["xdc_digest"] = manifest.get("digest", {}).get("constraints", "")[:12]

    if verdict:
        record["blockers"] = verdict.get("blockers")
        record["criticals"] = verdict.get("criticals")
        record["bringup_ok"] = verdict.get("bringup_ok")
        record["signoff_ok"] = verdict.get("signoff_ok")

    if extra:
        record.update(extra)
    return record


def delta(current, previous, metric):
    """Return (delta, verdict) for one metric against the previous run.

    ``verdict`` is 'better', 'worse', 'same', or None when not comparable.
    """
    if previous is None:
        return None, None
    new_value = current.get(metric)
    old_value = previous.get(metric)
    if new_value is None or old_value is None:
        return None, None

    change = new_value - old_value
    if abs(change) < 1e-9:
        return 0.0, "same"
    if metric in _HIGHER_IS_BETTER:
        return change, "better" if change > 0 else "worse"
    return change, "better" if change < 0 else "worse"


def endpoint_diff(current_paths, previous_endpoints):
    """Compare violating endpoints between runs.

    Returns newly-appeared and newly-resolved endpoint names, which is usually
    a faster read than the slack numbers when judging whether a fix landed.
    """
    current = set()
    for path in current_paths:
        slack = path.get("slack_ns")
        if path.get("destination") and slack is not None and slack < 0:
            current.add(path["destination"])

    if previous_endpoints is None:
        return {"current": sorted(current), "new": [], "resolved": []}

    previous = set(previous_endpoints)
    return {
        "current": sorted(current),
        "new": sorted(current - previous),
        "resolved": sorted(previous - current),
    }


def previous_record(records):
    """Return the run before the one just appended, or None."""
    return records[-1] if records else None
