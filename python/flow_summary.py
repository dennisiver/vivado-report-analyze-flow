#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Combine the per-stage assessments into one entry point for the agent.

Each stage writes its own ``risk_<stage>.json``; this rolls them up so a
question like "can this go on the bench?" has a single answer rather than one
per stage. The verdicts combine pessimistically: a stage that blocks anything
blocks the flow, and a stage that never ran is never counted as a pass.

Standard library only, Python 3.4+.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from risk_rules import (BLOCKER, CRITICAL, SEVERITY_ORDER,  # noqa: E402
                        dedupe_findings)


def _load(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError):
        return None


def collect(outdir, stages):
    """Load each stage's assessment, marking missing ones as not run."""
    collected = []
    for stage in stages:
        assessment = _load(os.path.join(outdir, "risk_{0}.json".format(stage)))
        collected.append({
            "stage": stage,
            "ran": assessment is not None,
            "assessment": assessment,
        })
    return collected


def combine(collected):
    """Roll per-stage verdicts up, pessimistically."""
    counts = dict((severity, 0) for severity in SEVERITY_ORDER)
    blockers = []
    bringup_ok = True
    signoff_ok = True
    missing = []

    for entry in collected:
        if not entry["ran"]:
            missing.append(entry["stage"])
            # A stage that did not run tells us nothing, so it cannot clear
            # sign-off. Bring-up is left alone: absence is not evidence of a
            # hardware hazard, only of an unfinished flow.
            signoff_ok = False
            continue

        verdict = entry["assessment"].get("verdict") or {}
        for severity, value in (verdict.get("counts") or {}).items():
            counts[severity] = counts.get(severity, 0) + value
        bringup_ok = bringup_ok and verdict.get("bringup_ok", False)
        signoff_ok = signoff_ok and verdict.get("signoff_ok", False)

        for item in entry["assessment"].get("findings") or []:
            if item.get("severity") == BLOCKER and not item.get("waived"):
                blockers.append(dict(item, stage=entry["stage"]))

    return {
        "counts": counts,
        "blockers": dedupe_findings(blockers),
        "bringup_ok": bringup_ok,
        "signoff_ok": signoff_ok,
        "stages_not_run": missing,
    }


def render(collected, combined, outdir):
    lines = ["# 合成流程總覽 —— {0}".format(
        datetime.datetime.now().replace(microsecond=0).isoformat()), ""]

    lines.append("| 判定 | 結果 |")
    lines.append("|---|---|")
    lines.append("| 可否上板 bring-up | {0} |".format(
        "✅ 可以" if combined["bringup_ok"] else "❌ 不可"))
    lines.append("| 可否 sign-off | {0} |".format(
        "✅ 可以" if combined["signoff_ok"] else "❌ 不可"))
    lines.append("")

    lines.append("| 階段 | 狀態 | BLOCKER | CRITICAL | 詳細 |")
    lines.append("|---|---|---|---|---|")
    for entry in collected:
        if not entry["ran"]:
            lines.append("| `{0}` | 未執行 | - | - | - |".format(entry["stage"]))
            continue
        verdict = entry["assessment"].get("verdict") or {}
        counts = verdict.get("counts") or {}
        lines.append("| `{0}` | {1} | {2} | {3} | `latest_{0}.md` |".format(
            entry["stage"],
            "✅" if verdict.get("bringup_ok") else "❌",
            counts.get(BLOCKER, 0), counts.get(CRITICAL, 0)))
    lines.append("")

    if combined["blockers"]:
        lines.append("## 必須先解決（BLOCKER）")
        lines.append("")
        for index, item in enumerate(combined["blockers"], 1):
            lines.append("{0}. `[{1}]` **{2}**".format(
                index, item["stage"], item["title"]))
        lines.append("")
    else:
        lines.append("目前沒有阻擋上板驗證的項目。")
        lines.append("")

    if combined["stages_not_run"]:
        lines.append("> ⚠ 未執行的階段：{0}。".format(
            "、".join("`{0}`".format(s) for s in combined["stages_not_run"])))
        lines.append("> 這些階段沒有被檢查，不代表它們沒有問題。")
        lines.append("")

    lines.append("各階段詳細請讀對應的 `latest_<stage>.md`；"
                 "完整風險說明在 `risk_<stage>.md`。")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Roll the per-stage assessments into one overview.")
    parser.add_argument("--outdir", default="timing_analysis")
    parser.add_argument("--stages", nargs="+", required=True)
    args = parser.parse_args(argv)

    collected = collect(args.outdir, args.stages)
    combined = combine(collected)

    if not os.path.isdir(args.outdir):
        os.makedirs(args.outdir)

    target = os.path.join(args.outdir, "latest_flow.md")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(render(collected, combined, args.outdir))
    with open(os.path.join(args.outdir, "flow_summary.json"),
              "w", encoding="utf-8") as handle:
        json.dump({"stages": [e["stage"] for e in collected],
                   "combined": combined},
                  handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")

    print("Flow overview: {0}".format(target))
    print("bring-up {0}, sign-off {1}".format(
        "OK" if combined["bringup_ok"] else "BLOCKED",
        "OK" if combined["signoff_ok"] else "BLOCKED"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
