#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The human sign-off report: complete, auditable, read once.

This is the opposite artifact to ``latest_<stage>.md``. That one is written to
be small because an agent has to hold it in a context window. This one is
written to be complete, because a person is putting their name to it and needs
to see everything that was checked, everything that was not, and everything
that was accepted anyway.

Status of every checklist item is derived from artifacts actually on disk. A
stage that did not run is reported as NOT CHECKED, never as passing -- the
distinction between "we looked and it was fine" and "we never looked" is the
whole point of a sign-off document.

Standard library only, Python 3.4+.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from risk_rules import BLOCKER, CRITICAL, dedupe_findings  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
WAIVED = "WAIVED"
NOT_CHECKED = "NOT CHECKED"

STATUS_MARK = {PASS: "✅ PASS", FAIL: "❌ FAIL",
               WAIVED: "⚠ WAIVED", NOT_CHECKED: "— NOT CHECKED"}


def _load(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError):
        return None


def _findings_with_prefix(assessments, prefixes):
    """Every finding whose rule id starts with one of ``prefixes``."""
    matched = []
    for stage, assessment in assessments:
        for item in (assessment or {}).get("findings") or []:
            if any(item["id"].startswith(prefix) for prefix in prefixes):
                matched.append(dict(item, stage=stage))
    return dedupe_findings(matched)


def _status_from(findings, ran):
    """PASS / FAIL / WAIVED / NOT CHECKED for one checklist row."""
    if not ran:
        return NOT_CHECKED, []
    blocking = [f for f in findings
                if f.get("blocks_bringup") or f.get("blocks_signoff")]
    if blocking:
        return FAIL, blocking
    waived = [f for f in findings if f.get("waived")]
    if waived:
        return WAIVED, waived
    return PASS, []


# Each row: (label, rule-id prefixes, name of the artifact proving it ran)
CHECKLIST = [
    ("0. 工具與環境版本", ("ENV.",), "environment"),
    ("1. 靜態檔案檢查（file list）", ("FILELIST.",), "filelist"),
    ("2. 專案稽核（fileset / constraint）", ("PROV.PREFLIGHT",), "preflight"),
    ("3. Elaboration 與 log 訊息", ("LOG.",), "log"),
    ("4. 時序（timing）", ("TIMING.",), "timing"),
    ("5. 跨時脈域（CDC / clock interaction）", ("CDC.", "CLK."), "cdc"),
    ("6. 設計規則與方法學（DRC / methodology）", ("DRC.", "METH."), "drc"),
    ("7. 資源使用率與 QoR", ("UTIL.", "CTRLSET.", "QOR."), "utilization"),
    ("8. IP 狀態", ("IP.",), "ip_status"),
    ("9. Bitstream", ("BITSTREAM.",), "bitstream"),
    ("10. 輸入版本可追溯性", ("PROV.DIRTY",), "manifest"),
]


def _artifact_ran(name, context):
    """Whether the evidence for a checklist row was actually produced."""
    return bool(context.get(name))


def build(outdir, stages, project_name=None):
    """Gather every artifact and derive the checklist."""
    assessments = []
    for stage in stages:
        assessments.append(
            (stage, _load(os.path.join(outdir, "risk_{0}.json".format(stage)))))

    environment = _load(os.path.join(outdir, "manifests",
                                     "environment_compare.json"))
    filelist = _load(os.path.join(outdir, "manifests", "filelist_check.json"))
    flow = _load(os.path.join(outdir, "flow_summary.json"))

    manifest = None
    preflight = None
    for stage in stages:
        manifest = manifest or _load(os.path.join(
            outdir, "manifests", "compare_{0}.json".format(stage)))
        preflight = preflight or _load(os.path.join(
            outdir, "manifests", "preflight_{0}.json".format(stage)))

    ran_any = any(a for _, a in assessments)
    context = {
        "environment": environment,
        "filelist": filelist,
        "preflight": preflight,
        "manifest": manifest,
        "log": ran_any,
        "timing": ran_any,
        "cdc": ran_any,
        "drc": ran_any,
        "utilization": ran_any,
        "ip_status": ran_any,
        "bitstream": ran_any,
    }

    rows = []
    for label, prefixes, artifact in CHECKLIST:
        findings = _findings_with_prefix(assessments, prefixes)
        status, evidence = _status_from(findings, _artifact_ran(artifact, context))
        rows.append({"label": label, "status": status,
                     "findings": findings, "evidence": evidence})

    return {
        "project": project_name or "",
        "timestamp": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "stages": stages,
        "assessments": assessments,
        "environment": environment,
        "filelist": filelist,
        "manifest": manifest,
        "flow": flow,
        "rows": rows,
    }


def _verdicts(data):
    flow = (data.get("flow") or {}).get("combined")
    if flow:
        return flow.get("bringup_ok"), flow.get("signoff_ok")

    bringup = signoff = True
    seen = False
    for _, assessment in data["assessments"]:
        if not assessment:
            signoff = False
            continue
        seen = True
        verdict = assessment.get("verdict") or {}
        bringup = bringup and verdict.get("bringup_ok", False)
        signoff = signoff and verdict.get("signoff_ok", False)
    if not seen:
        return None, None
    return bringup, signoff


def render(data):
    lines = []
    title = "FPGA 合成 Sign-off 報告"
    if data["project"]:
        title += " —— {0}".format(data["project"])
    lines.append("# {0}".format(title))
    lines.append("")
    lines.append("產出時間：{0}".format(data["timestamp"]))
    lines.append("")
    lines.append("> 這份報告是給**人**審閱簽核用的，內容刻意完整。"
                 "AI agent 請改讀 `latest_flow.md` 與 `latest_<stage>.md`。")
    lines.append("")

    # --- verdict ---------------------------------------------------------
    bringup, signoff = _verdicts(data)
    lines.append("## 一、判定結論")
    lines.append("")
    lines.append("| 判定 | 結果 | 意義 |")
    lines.append("|---|---|---|")
    lines.append("| 可否上板 bring-up | {0} | 實機驗證的結果是否可信 |".format(
        "✅ 可以" if bringup else ("❌ 不可" if bringup is not None else "— 未知")))
    lines.append("| 可否 sign-off | {0} | 是否可作為正式交付 |".format(
        "✅ 可以" if signoff else ("❌ 不可" if signoff is not None else "— 未知")))
    lines.append("")

    # --- checklist -------------------------------------------------------
    lines.append("## 二、檢查項目清單")
    lines.append("")
    lines.append("| 檢查項目 | 結果 | 發現 |")
    lines.append("|---|---|---|")
    for row in data["rows"]:
        detail = "-"
        if row["status"] == FAIL:
            detail = "{0} 項阻擋".format(len(row["evidence"]))
        elif row["status"] == WAIVED:
            detail = "{0} 項已豁免".format(len(row["evidence"]))
        elif row["findings"]:
            detail = "{0} 項提醒".format(len(row["findings"]))
        lines.append("| {0} | {1} | {2} |".format(
            row["label"], STATUS_MARK[row["status"]], detail))
    lines.append("")

    not_checked = [r["label"] for r in data["rows"] if r["status"] == NOT_CHECKED]
    if not_checked:
        lines.append("> ⚠ **未檢查的項目**（本報告並未涵蓋，"
                     "不代表這些面向沒有問題）：")
        for label in not_checked:
            lines.append("> - {0}".format(label))
        lines.append("")

    # --- environment -----------------------------------------------------
    lines.append("## 三、環境與工具版本")
    lines.append("")
    environment = ((data.get("environment") or {}).get("environment")) or {}
    if environment:
        vivado = environment.get("vivado") or {}
        operating = environment.get("os") or {}
        python = environment.get("python") or {}
        host = environment.get("host") or {}
        lines.append("| 項目 | 值 |")
        lines.append("|---|---|")
        lines.append("| Vivado | {0} {1} |".format(
            vivado.get("version") or "?", vivado.get("build") or ""))
        lines.append("| Vivado 安裝路徑 | `{0}` |".format(
            vivado.get("install_root") or "?"))
        lines.append("| OS | {0} |".format(operating.get("pretty_name") or "?"))
        lines.append("| Kernel | {0} |".format(operating.get("kernel") or "?"))
        lines.append("| 主機 | {0} |".format(host.get("hostname") or "?"))
        lines.append("| Python | {0} (`{1}`) |".format(
            python.get("version") or "?", python.get("executable") or "?"))
    else:
        lines.append("_未記錄環境資訊（階段 0 未執行）。_")
    lines.append("")

    # --- inputs ----------------------------------------------------------
    lines.append("## 四、輸入版本")
    lines.append("")
    manifest = ((data.get("manifest") or {}).get("manifest")) or {}
    if manifest:
        git = manifest.get("git") or {}
        lines.append("| 項目 | 值 |")
        lines.append("|---|---|")
        lines.append("| RTL git commit | `{0}` |".format(
            git.get("commit") or "（非 git 專案）"))
        lines.append("| 分支 | {0} |".format(git.get("branch") or "-"))
        dirty = git.get("dirty_inputs") or []
        lines.append("| 未 commit 的設計檔 | {0} |".format(
            "**{0} 個 —— 此 bitstream 無法由 git 重現**".format(len(dirty))
            if dirty else "無"))
        for path in dirty[:20]:
            lines.append("| | `{0}` |".format(path))
        lines.append("| RTL digest | `{0}` |".format(
            (manifest.get("digest") or {}).get("sources", "")[:16]))
        lines.append("| XDC digest | `{0}` |".format(
            (manifest.get("digest") or {}).get("constraints", "")[:16]))
    else:
        lines.append("_未記錄輸入版本。_")

    filelist = data.get("filelist") or {}
    if filelist.get("available"):
        lines.append("")
        lines.append("File list：{0} 個檔案，{1} 個缺失。".format(
            filelist.get("file_count", 0), len(filelist.get("missing_files") or [])))
        # Whoever signs this has to be able to see what it does not cover.
        excluded = filelist.get("excluded") or []
        patterns = filelist.get("exclude_patterns") or []
        if patterns:
            lines.append("**另有 {0} 個檔案被排除、未經檢查**，"
                         "排除樣式：{1}。".format(
                             len(excluded),
                             "、".join("`{0}`".format(p) for p in patterns)))
    lines.append("")

    # --- findings --------------------------------------------------------
    lines.append("## 五、發現項目明細")
    lines.append("")
    for severity in (BLOCKER, CRITICAL):
        group = []
        for stage, assessment in data["assessments"]:
            for item in (assessment or {}).get("findings") or []:
                if item["severity"] == severity and not item.get("waived"):
                    group.append(dict(item, stage=stage))
        group = dedupe_findings(group)
        if not group:
            continue
        lines.append("### {0}".format(severity))
        lines.append("")
        for index, item in enumerate(group, 1):
            lines.append("**{0}. [`{1}`] {2}**".format(
                index, item["stage"], item["title"]))
            lines.append("")
            lines.append("- 規則：`{0}`　來源：{1}".format(item["id"], item["source"]))
            lines.append("- 阻擋上板：{0}　阻擋 sign-off：{1}".format(
                "是" if item["blocks_bringup"] else "否",
                "是" if item["blocks_signoff"] else "否"))
            lines.append("- 風險：{0}".format(item["risk"]))
            lines.append("- 建議：{0}".format(item["action"]))
            lines.append("- 佐證 digest：`{0}`（可用於 waiver）".format(
                item.get("digest", "-")))
            lines.append("")
    if not any(f["severity"] in (BLOCKER, CRITICAL)
               for _, a in data["assessments"]
               for f in (a or {}).get("findings") or []):
        lines.append("沒有 BLOCKER 或 CRITICAL 等級的發現。")
        lines.append("")

    # --- waivers ---------------------------------------------------------
    lines.append("## 六、已核准的豁免項目")
    lines.append("")
    waived = [dict(f, stage=stage)
              for stage, a in data["assessments"]
              for f in (a or {}).get("findings") or [] if f.get("waived")]
    if waived:
        lines.append("| 階段 | 項目 | 原本嚴重度 | 理由 | 核准人 | 日期 |")
        lines.append("|---|---|---|---|---|---|")
        for item in waived:
            waiver = item.get("waiver") or {}
            lines.append("| `{0}` | {1} | {2} | {3} | {4} | {5} |".format(
                item["stage"], item["title"][:50], item["severity"],
                waiver.get("reason", "-"), waiver.get("approved_by", "-"),
                waiver.get("date", "-")))
        lines.append("")
        lines.append("_這些項目已由人工審查並接受，因此不阻擋 sign-off，"
                     "但風險仍然存在。佐證內容一旦改變，豁免會自動失效。_")
    else:
        lines.append("無。")
    lines.append("")

    expired = [e for _, a in data["assessments"]
               for e in ((a or {}).get("waivers") or {}).get("expired") or []]
    if expired:
        lines.append("### 已失效的豁免")
        lines.append("")
        lines.append("下列豁免因為佐證內容改變而自動失效，需要重新審查：")
        for entry in expired:
            lines.append("- `{0}`：{1}（digest {2} → {3}）".format(
                entry["id"], entry.get("reason", ""),
                entry.get("recorded_digest"), entry.get("current_digest")))
        lines.append("")

    # --- signature -------------------------------------------------------
    lines.append("## 七、簽核")
    lines.append("")
    lines.append("| 角色 | 姓名 | 日期 | 簽名 |")
    lines.append("|---|---|---|---|")
    lines.append("| 設計者 | | | |")
    lines.append("| 審查者 | | | |")
    lines.append("| 核准者 | | | |")
    lines.append("")
    lines.append("簽核前請確認：")
    lines.append("")
    lines.append("- [ ] 第二節中沒有 FAIL 的項目，或所有 FAIL 都已有正式豁免")
    lines.append("- [ ] 第二節列出的「未檢查項目」都已確認可以接受")
    lines.append("- [ ] 第三節的工具版本與專案要求相符")
    lines.append("- [ ] 第四節顯示沒有未 commit 的設計檔（否則此結果無法重現）")
    lines.append("- [ ] 第六節的每一項豁免都經過審查")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Produce the human-facing sign-off report.")
    parser.add_argument("--outdir", default="timing_analysis")
    parser.add_argument("--stages", nargs="+", default=["impl_1"])
    parser.add_argument("--project-name")
    parser.add_argument("--output",
                        help="explicit output path (default: "
                             "<outdir>/signoff_<timestamp>.md)")
    args = parser.parse_args(argv)

    data = build(args.outdir, args.stages, args.project_name)
    text = render(data)

    if not os.path.isdir(args.outdir):
        os.makedirs(args.outdir)
    target = args.output or os.path.join(
        args.outdir, "signoff_{0}.md".format(
            data["timestamp"].replace(":", "").replace("-", "")))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)

    # A stable filename alongside the timestamped one, for tooling.
    latest = os.path.join(args.outdir, "signoff_latest.md")
    with open(latest, "w", encoding="utf-8") as handle:
        handle.write(text)

    failed = [r["label"] for r in data["rows"] if r["status"] == FAIL]
    print("Sign-off report: {0}".format(target))
    print("{0} 項檢查失敗，{1} 項未檢查".format(
        len(failed),
        len([r for r in data["rows"] if r["status"] == NOT_CHECKED])))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
