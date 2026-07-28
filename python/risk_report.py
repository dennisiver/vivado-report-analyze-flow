#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the risk assessment, in Traditional Chinese with English terms kept.

Two renderings, matching the progressive-disclosure rule the whole flow follows:

  render_verdict_block  the compact block placed at the top of latest_<run>.md:
                        the two verdicts, the counts, and the BLOCKERs only
  render_full_report    risk_<run>.md, every finding at every severity, read
                        only when someone actually asks about the details

Standard library only, Python 3.4+.
"""

from risk_rules import BLOCKER, CRITICAL, INFO, SEVERITY_ORDER, WARNING


SEVERITY_LABEL = {
    BLOCKER: "BLOCKER（阻擋上板驗證）",
    CRITICAL: "CRITICAL（阻擋 sign-off）",
    WARNING: "WARNING（影響穩定性與可重複性）",
    INFO: "INFO（觀察項）",
}


def _verdict_line(ok, blocker_count):
    if ok:
        return "✅ 可以"
    return "❌ 不可 —— {0} 項阻擋".format(blocker_count)


def _unavailable_reports(findings):
    return [f for f in findings if f["id"] == "META.REPORT_UNAVAILABLE"]


def render_verdict_block(assessment, risk_report_path=None):
    """Compact verdict section for the top of latest_<run>.md."""
    findings = assessment["findings"]
    verdict = assessment["verdict"]
    counts = verdict["counts"]

    lines = ["## 驗證風險判定", ""]
    lines.append("| 判定 | 結果 |")
    lines.append("|---|---|")
    lines.append("| 可否上板 bring-up | {0} |".format(
        _verdict_line(verdict["bringup_ok"], verdict["bringup_blocker_count"])))
    lines.append("| 可否 sign-off | {0} |".format(
        _verdict_line(verdict["signoff_ok"], verdict["signoff_blocker_count"])))
    lines.append("")

    lines.append("| 嚴重度 | 數量 |")
    lines.append("|---|---|")
    for severity in SEVERITY_ORDER:
        lines.append("| {0} | {1} |".format(SEVERITY_LABEL[severity],
                                            counts.get(severity, 0)))
    lines.append("")

    blockers = [f for f in findings if f["severity"] == BLOCKER]
    if blockers:
        lines.append("### 必須先解決（BLOCKER）")
        lines.append("")
        for index, item in enumerate(blockers, 1):
            lines.append("{0}. **{1}**".format(index, item["title"]))
            lines.append("   風險：{0}".format(item["risk"]))
            lines.append("   建議：{0}".format(item["action"]))
            lines.append("")
    else:
        lines.append("目前沒有阻擋上板驗證的項目。")
        lines.append("")

    unavailable = _unavailable_reports(findings)
    if unavailable:
        names = "、".join(
            item["title"].replace("未完成的分析：", "") for item in unavailable)
        lines.append("> ⚠ **未檢查的項目**：{0}。".format(names))
        lines.append("> 這些面向沒有被涵蓋，不代表它們沒有問題。")
        lines.append("")

    if counts.get(CRITICAL) or counts.get(WARNING):
        lines.append("完整風險報告（含 CRITICAL 與 WARNING 的詳細說明）：`{0}`".format(
            risk_report_path or "risk_<run>.md"))
        lines.append("")

    return "\n".join(lines)


def render_full_report(assessment, stage, timestamp, design=None,
                       summary_path=None):
    """The complete risk document, every severity, with evidence."""
    findings = assessment["findings"]
    verdict = assessment["verdict"]

    lines = ["# FPGA 驗證風險報告 —— `{0}` —— {1}".format(stage, timestamp), ""]
    if design:
        lines.append("設計：`{0}`".format(design))
        lines.append("")

    lines.append("## 結論")
    lines.append("")
    lines.append("| 判定 | 結果 | 說明 |")
    lines.append("|---|---|---|")
    lines.append("| 可否上板 bring-up | {0} | 是否會讓實機驗證的結果不可信 |".format(
        _verdict_line(verdict["bringup_ok"], verdict["bringup_blocker_count"])))
    lines.append("| 可否 sign-off | {0} | 是否可作為正式交付 |".format(
        _verdict_line(verdict["signoff_ok"], verdict["signoff_blocker_count"])))
    lines.append("")

    lines.append("嚴重度是由上面兩個判定推導出來的：")
    lines.append("")
    lines.append("- **BLOCKER** —— 兩者皆阻擋。例如 hold 違規：與頻率無關，"
                 "降頻也無法迴避，帶上板得到的任何結論都不可信。")
    lines.append("- **CRITICAL** —— 只阻擋 sign-off。例如 setup 違規："
                 "可以降頻先做 bring-up，但原頻率下的行為未經驗證。")
    lines.append("- **WARNING** —— 影響穩定性或結果的可重複性。")
    lines.append("- **INFO** —— 觀察項。")
    lines.append("")

    unavailable = _unavailable_reports(findings)
    if unavailable:
        lines.append("> ⚠ 本報告**未涵蓋**下列面向，因為對應的分析沒有執行成功：")
        for item in unavailable:
            lines.append("> - {0}（{1}）".format(
                item["title"].replace("未完成的分析：", ""), item["action"]))
        lines.append("")

    for severity in SEVERITY_ORDER:
        group = [f for f in findings if f["severity"] == severity]
        if not group:
            continue
        lines.append("## {0}".format(SEVERITY_LABEL[severity]))
        lines.append("")
        for index, item in enumerate(group, 1):
            lines.append("### {0}. {1}".format(index, item["title"]))
            lines.append("")
            lines.append("- 規則：`{0}`　來源：`{1}`".format(item["id"], item["source"]))
            lines.append("- 阻擋上板：{0}　阻擋 sign-off：{1}".format(
                "是" if item["blocks_bringup"] else "否",
                "是" if item["blocks_signoff"] else "否"))
            lines.append("- **風險**：{0}".format(item["risk"]))
            lines.append("- **建議**：{0}".format(item["action"]))
            evidence = _format_evidence(item.get("evidence"))
            if evidence:
                lines.append("- 佐證：{0}".format(evidence))
            lines.append("")

    if summary_path:
        lines.append("---")
        lines.append("")
        lines.append("時序摘要：`{0}`".format(summary_path))
        lines.append("")

    return "\n".join(lines)


def _format_evidence(evidence):
    if not evidence:
        return ""
    parts = []
    for key in sorted(evidence):
        value = evidence[key]
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            shown = "、".join(str(entry) for entry in value[:5])
            if len(value) > 5:
                shown += " …"
            parts.append("{0}={1}".format(key, shown))
        elif isinstance(value, float):
            parts.append("{0}={1:.3f}".format(key, value))
        else:
            parts.append("{0}={1}".format(key, value))
    return "　".join(parts)
