#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consolidate the pre-build stages (0-2) into one verdict, and gate stage 3.

`make check` already stopped at the first failing stage -- Make aborts on the
first prerequisite that returns non-zero. Pure fail-fast is the wrong grain
here though: stages 0, 1 and 2 all finish in seconds, so stopping at the first
one means the human makes N round trips for N problems. Stage 3 (elaboration)
is the expensive one, minutes rather than seconds.

So the cheap stages all run and report together, and only the expensive stage
is gated. This module is the "report together" half: it reads what stages 0-2
already wrote, grades each through the same rule engine the build stages use,
rolls them up, and exits non-zero when stage 3 should not run.

No new rules are defined here. ``risk_rules.evaluate()`` already accepts each of
these artifacts on its own, and ``flow_summary.combine()`` already knows that a
stage which did not run cannot clear sign-off -- which is exactly the semantics
a SKIPPED stage needs.
"""

from __future__ import print_function

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import flow_summary as flow_mod          # noqa: E402
import manifest as manifest_mod          # noqa: E402
import risk_rules as risk_mod            # noqa: E402
import waivers as waivers_mod            # noqa: E402

SKIPPED = "skipped"

# (key, 顯示名稱, 產生它的指令)
STAGES = [
    ("env", "階段 0：工具與環境版本", "make check-env"),
    ("files", "階段 1：靜態檔案檢查", "make check-files"),
    ("project", "階段 2：專案稽核", "make check-project"),
]


def _written_assessment(outdir, stage):
    """The ``risk_<stage>.json`` the stage itself wrote, when it wrote one."""
    data = manifest_mod.load_json(
        os.path.join(outdir, "risk_{0}.json".format(stage)))
    if isinstance(data, dict) and isinstance(data.get("verdict"), dict):
        return data
    return None


def _assess_env(outdir, waiver_data):
    written = _written_assessment(outdir, "env")
    if written:
        return written
    # Fall back to grading the raw artifact, so an outdir produced by an older
    # version of this flow still reports rather than showing a blank stage.
    compare = manifest_mod.load_json(
        os.path.join(outdir, "manifests", "environment_compare.json"))
    if not compare:
        return None
    return risk_mod.evaluate(environment=compare.get("environment"),
                             environment_comparison=compare.get("comparison"),
                             waiver_data=waiver_data)


def _assess_files(outdir, waiver_data):
    written = _written_assessment(outdir, "files")
    if written:
        return written
    result = manifest_mod.load_json(
        os.path.join(outdir, "manifests", "filelist_check.json"))
    if not result:
        return None
    return risk_mod.evaluate(filelist=result, waiver_data=waiver_data)


def _assess_project(outdir, run, waiver_data):
    preflight = manifest_mod.load_json(
        os.path.join(outdir, "manifests", "preflight_{0}.json".format(run)))
    if not preflight:
        return None
    manifest = manifest_mod.load_json(
        os.path.join(outdir, "manifests",
                     "manifest_{0}_current.json".format(run)))
    return risk_mod.evaluate(preflight=preflight, manifest=manifest,
                             waiver_data=waiver_data)


_ASSESSORS = {
    "env": lambda outdir, run, waivers: _assess_env(outdir, waivers),
    "files": lambda outdir, run, waivers: _assess_files(outdir, waivers),
    "project": lambda outdir, run, waivers: _assess_project(outdir, run,
                                                            waivers),
}


def collect(outdir, run, statuses, waiver_data=None):
    """One entry per pre-check stage, shaped for ``flow_summary.combine()``."""
    collected = []
    for key, label, command in STAGES:
        status = statuses.get(key)
        assessment = None
        if status != SKIPPED:
            assessment = _ASSESSORS[key](outdir, run, waiver_data)

        collected.append({
            "stage": key,
            "label": label,
            "command": command,
            "status": status,
            "ran": assessment is not None,
            "assessment": assessment,
        })
    return collected


def gate(collected, combined):
    """Should stage 3 (elaboration) run? Returns ``(allowed, reasons)``.

    Deliberately stricter than the exit codes alone: the build stages exit 0
    even when they found BLOCKERs, so gating on status would let a design with
    a known-missing module burn five minutes proving it.
    """
    reasons = []

    for entry in collected:
        if entry["status"] == SKIPPED:
            reasons.append("{0} 未執行（相依的階段先失敗了）".format(
                entry["label"]))
        elif entry["status"] not in (None, 0):
            reasons.append("{0} 以狀態 {1} 結束".format(
                entry["label"], entry["status"]))
        elif not entry["ran"]:
            reasons.append("{0} 沒有留下可判讀的結果".format(entry["label"]))

    if not combined["bringup_ok"]:
        reasons.append("有 {0} 項 BLOCKER".format(len(combined["blockers"])))

    return (not reasons), reasons


def render(collected, combined, allowed, reasons):
    now = datetime.datetime.now().replace(microsecond=0).isoformat()
    lines = ["# 建置前檢查（pre-check）—— {0}".format(now), ""]

    if allowed:
        lines.append("**結論：可以進入 elaboration。**")
    else:
        lines.append("**結論：不進入 elaboration。** 原因：")
        for reason in reasons:
            lines.append("- {0}".format(reason))
    lines.append("")

    lines.append("| 階段 | 狀態 | BLOCKER | CRITICAL | WARNING |")
    lines.append("|---|---|---|---|---|")
    for entry in collected:
        if entry["status"] == SKIPPED:
            lines.append("| {0} | ⏭ 未執行 | - | - | - |".format(entry["label"]))
            continue
        if not entry["ran"]:
            lines.append("| {0} | ⚠ 無結果 | - | - | - |".format(entry["label"]))
            continue
        counts = (entry["assessment"].get("verdict") or {}).get("counts") or {}
        mark = "✅" if entry["status"] in (None, 0) else "❌"
        lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
            entry["label"], mark,
            counts.get(risk_mod.BLOCKER, 0),
            counts.get(risk_mod.CRITICAL, 0),
            counts.get(risk_mod.WARNING, 0)))
    lines.append("")

    if combined["blockers"]:
        lines.append("## 必須先解決（BLOCKER）")
        lines.append("")
        for index, item in enumerate(combined["blockers"], 1):
            lines.append("{0}. **{1}**".format(index, item["title"]))
            lines.append("   - 風險：{0}".format(item["risk"]))
            lines.append("   - 處理：{0}".format(item["action"]))
        lines.append("")

    criticals = [f for entry in collected if entry["ran"]
                 for f in entry["assessment"]["findings"]
                 if f["severity"] == risk_mod.CRITICAL and not f.get("waived")]
    if criticals:
        lines.append("## 上板前應確認（CRITICAL）")
        lines.append("")
        for item in risk_mod.dedupe_findings(criticals):
            lines.append("- **{0}** —— {1}".format(item["title"],
                                                   item["action"]))
        lines.append("")

    not_run = [e for e in collected if not e["ran"]]
    if not_run:
        lines.append("> ⚠ 以下階段沒有留下結果，**未經檢查不等於沒問題**：")
        for entry in not_run:
            lines.append("> - {0}（`{1}`）".format(entry["label"],
                                                   entry["command"]))
        lines.append("")

    return "\n".join(lines) + "\n"


def _parse_status(text):
    if "=" not in text:
        raise argparse.ArgumentTypeError(
            "--status 需要 name=value 格式，收到 '{0}'".format(text))
    name, _, value = text.partition("=")
    if name not in dict((key, label) for key, label, _ in STAGES):
        raise argparse.ArgumentTypeError("未知的階段 '{0}'".format(name))
    if value == SKIPPED:
        return name, SKIPPED
    try:
        return name, int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "狀態必須是數字或 '{0}'，收到 '{1}'".format(SKIPPED, value))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Consolidate stages 0-2 and decide whether stage 3 runs.")
    parser.add_argument("--outdir", default="timing_analysis")
    parser.add_argument("--run", default="impl_1")
    parser.add_argument("--status", action="append", default=[],
                        type=_parse_status,
                        help="<stage>=<exit code|skipped>（可重複）")
    parser.add_argument("--waivers")
    args = parser.parse_args(argv)

    waiver_data = (waivers_mod.load_waivers(args.waivers)
                   if args.waivers and os.path.isfile(args.waivers) else None)

    statuses = dict(args.status)
    collected = collect(args.outdir, args.run, statuses, waiver_data)
    combined = flow_mod.combine(collected)
    allowed, reasons = gate(collected, combined)

    if not os.path.isdir(args.outdir):
        os.makedirs(args.outdir)

    target = os.path.join(args.outdir, "precheck_latest.md")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(render(collected, combined, allowed, reasons))

    with open(os.path.join(args.outdir, "precheck.json"),
              "w", encoding="utf-8") as handle:
        json.dump({"stages": [{"stage": e["stage"], "status": e["status"],
                               "ran": e["ran"]} for e in collected],
                   "combined": combined,
                   "elaborate_allowed": allowed,
                   "reasons": reasons,
                   # Same shape the banner reads from risk_<stage>.json, so the
                   # pre-check summary can drive a banner like any other stage.
                   "verdict": {
                       "counts": combined["counts"],
                       "blockers": combined["counts"].get(risk_mod.BLOCKER, 0),
                       "criticals": combined["counts"].get(risk_mod.CRITICAL, 0),
                       "waived": 0,
                       "bringup_ok": combined["bringup_ok"],
                       "signoff_ok": combined["signoff_ok"],
                       "bringup_blocker_count": len(combined["blockers"]),
                       "signoff_blocker_count": len(combined["blockers"]),
                   }},
                  handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")

    print("Pre-check 總覽：{0}".format(target))
    for entry in collected:
        if entry["status"] == SKIPPED:
            print("  {0}：未執行".format(entry["label"]))
        elif not entry["ran"]:
            print("  {0}：無結果".format(entry["label"]))
        else:
            counts = ((entry["assessment"].get("verdict") or {})
                      .get("counts") or {})
            print("  {0}：BLOCKER {1}、CRITICAL {2}".format(
                entry["label"], counts.get(risk_mod.BLOCKER, 0),
                counts.get(risk_mod.CRITICAL, 0)))

    if not allowed:
        print("")
        print("不進入 elaboration：")
        for reason in reasons:
            print("  - {0}".format(reason))
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())
