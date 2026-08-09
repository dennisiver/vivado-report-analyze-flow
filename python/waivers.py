#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Waivers: findings a human reviewed and accepted, so sign-off can proceed.

The danger with any waiver system is that it becomes a way to permanently mute
a real problem. Two rules keep that from happening here:

1. A waiver is bound to the *content* of the finding it covers, via a digest of
   its evidence. If the finding changes -- more instances, different cells, a
   worse count -- the digest stops matching and the waiver expires on its own,
   putting the finding back in front of a reviewer.

2. Waived findings are never hidden. They still appear, labelled as waived,
   with the reason and who approved them, and they get their own section of the
   sign-off report.

Waiver file format (project root, ``waivers.json``)::

    {"waivers": [
      {"id": "DRC.CRITICAL_WARNING",
       "match": {"rule": "RTSTAT-6"},
       "reason": "...", "approved_by": "...", "date": "2026-08-09",
       "evidence_digest": "ab12cd34"}
    ]}

Omit ``evidence_digest`` to accept whatever the finding currently says; the
digest is printed for each waivable finding so it can be pasted in.

Standard library only, Python 3.4+.
"""

import hashlib
import json
import os


DIGEST_LENGTH = 12


def evidence_digest(finding):
    """Stable short hash of a finding's identity and evidence."""
    payload = {
        "id": finding.get("id"),
        "evidence": finding.get("evidence") or {},
    }
    encoded = json.dumps(payload, sort_keys=True,
                         ensure_ascii=False).encode("utf-8", "replace")
    return hashlib.sha256(encoded).hexdigest()[:DIGEST_LENGTH]


def load_waivers(path):
    """Read waivers, degrading to an empty set rather than raising.

    A malformed waiver file must never waive anything: silently granting
    exemptions because a file failed to parse is exactly backwards.
    """
    result = {"available": False, "waivers": [], "reason": "", "path": path}
    if not path or not os.path.isfile(path):
        result["reason"] = "no waiver file"
        return result

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError) as error:
        result["reason"] = "could not read waivers ({0}) -- nothing waived".format(
            error)
        return result

    entries = data.get("waivers") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        result["reason"] = "waivers file has no 'waivers' list -- nothing waived"
        return result

    valid = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            valid.append(entry)
    result["available"] = True
    result["waivers"] = valid
    if len(valid) != len(entries):
        result["reason"] = "{0} malformed waiver entr(y/ies) ignored".format(
            len(entries) - len(valid))
    return result


def _matches(waiver, finding):
    if waiver.get("id") != finding.get("id"):
        return False
    criteria = waiver.get("match") or {}
    evidence = finding.get("evidence") or {}
    for key, expected in criteria.items():
        if str(evidence.get(key, "")) != str(expected):
            return False
    return True


def apply_waivers(findings, waiver_data):
    """Annotate findings with waiver state and return the updated list.

    Each finding gains ``digest``; a matched-and-current waiver additionally
    sets ``waived`` and clears the two blocking flags. A waiver whose digest no
    longer matches is reported as expired and does not take effect.
    """
    waivers = (waiver_data or {}).get("waivers") or []
    expired = []
    applied = []

    for finding in findings:
        finding["digest"] = evidence_digest(finding)
        finding["waived"] = False

        for waiver in waivers:
            if not _matches(waiver, finding):
                continue

            recorded = waiver.get("evidence_digest")
            if recorded and recorded != finding["digest"]:
                expired.append({
                    "id": finding["id"],
                    "reason": waiver.get("reason", ""),
                    "recorded_digest": recorded,
                    "current_digest": finding["digest"],
                })
                continue

            finding["waived"] = True
            finding["waiver"] = {
                "reason": waiver.get("reason", ""),
                "approved_by": waiver.get("approved_by", ""),
                "date": waiver.get("date", ""),
                "digest_pinned": bool(recorded),
            }
            # A waived finding no longer gates anything, but keeps its severity
            # so the report still shows how serious the accepted risk was.
            finding["blocks_bringup"] = False
            finding["blocks_signoff"] = False
            applied.append(finding["id"])
            break

    return {"findings": findings, "applied": applied, "expired": expired}


def format_report(waiver_data, result):
    lines = []
    if not (waiver_data or {}).get("available"):
        reason = (waiver_data or {}).get("reason") or "no waiver file"
        lines.append("  waivers : none ({0})".format(reason))
        return "\n".join(lines)

    lines.append("  waivers : {0} defined, {1} applied".format(
        len(waiver_data["waivers"]), len(result["applied"])))
    if waiver_data.get("reason"):
        lines.append("            note: {0}".format(waiver_data["reason"]))
    for entry in result["expired"]:
        lines.append("            EXPIRED {0} (evidence changed: {1} -> {2})".format(
            entry["id"], entry["recorded_digest"], entry["current_digest"]))
    return "\n".join(lines)
