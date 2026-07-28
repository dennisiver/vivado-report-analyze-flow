#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn parsed Vivado reports into graded, actionable FPGA validation risk.

The model is built on two independent questions, because in FPGA work they
genuinely have different answers:

  blocks_bringup  -- would taking this bitstream to the bench produce results
                     you cannot trust?
  blocks_signoff  -- would shipping this be unacceptable?

A setup violation fails only the second: you can clock the design slower and
still make real progress on the bench. A hold violation fails both, because
hold is frequency-independent -- slowing the clock down does nothing for it.
Encoding that distinction is the whole point; severity is derived from the two
flags rather than assigned by hand, so the grading can never contradict itself.

Standard library only, Python 3.4+.
"""

BLOCKER = "BLOCKER"
CRITICAL = "CRITICAL"
WARNING = "WARNING"
INFO = "INFO"

SEVERITY_ORDER = [BLOCKER, CRITICAL, WARNING, INFO]

DEFAULT_THRESHOLDS = {
    # Setup slack worse than this fraction of the clock period stops being
    # something a bench frequency reduction can compensate for.
    "setup_severe_fraction": 0.10,
    # Unconstrained endpoints above this share of all endpoints mean the
    # headline WNS no longer describes the design.
    "unconstrained_severe_fraction": 0.01,
    "route_dominated_percent": 70.0,
    "deep_logic_levels": 15,
    "util_critical_percent": 90.0,
    "util_high_percent": 80.0,
    "control_sets_per_register": 0.20,
}

# Vivado blocks write_bitstream on these by default; they also mean I/O has no
# defined electrical standard, which is a board-level hazard, not just a rule.
BITSTREAM_BLOCKING_DRC = frozenset(["NSTD-1", "UCIO-1"])

# Xilinx's own "your timing results are not trustworthy" methodology rules.
METHODOLOGY_UNSAFE_TIMING = frozenset(["TIMING-6", "TIMING-7", "TIMING-9"])
METHODOLOGY_CLOCK_PATH = frozenset(["TIMING-14", "TIMING-15"])

# Reports whose absence leaves a real hole in the assessment. control_sets is
# an optimisation hint, so losing it is not a sign-off concern.
SAFETY_RELEVANT_REPORTS = frozenset(
    ["utilization", "drc", "methodology", "cdc", "clock_interaction"])


def severity_of(blocks_bringup, blocks_signoff, notable=True):
    if blocks_bringup:
        return BLOCKER
    if blocks_signoff:
        return CRITICAL
    return WARNING if notable else INFO


def finding(identifier, title, risk, action, blocks_bringup=False,
            blocks_signoff=False, notable=True, evidence=None, source=""):
    return {
        "id": identifier,
        "severity": severity_of(blocks_bringup, blocks_signoff, notable),
        "blocks_bringup": bool(blocks_bringup),
        "blocks_signoff": bool(blocks_signoff),
        "title": title,
        "risk": risk,
        "action": action,
        "evidence": evidence or {},
        "source": source,
    }


def _clock_period(timing):
    """Reference period for scaling setup slack: the tightest clock."""
    paths = (timing or {}).get("paths") or []
    for path in paths:
        if path.get("delay_kind") == "max" and path.get("requirement_ns"):
            return path["requirement_ns"]
    periods = [clock.get("period_ns") for clock in (timing or {}).get("clocks") or []
               if clock.get("period_ns")]
    return min(periods) if periods else None


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

def _evaluate_timing(timing, thresholds):
    findings = []
    if not timing:
        return findings

    summary = timing.get("summary") or {}
    checks = (timing.get("check_timing") or {}).get("items") or {}

    whs = summary.get("whs")
    if whs is not None and whs < 0:
        findings.append(finding(
            "TIMING.HOLD_VIOLATION",
            "Hold 違規：WHS = {0:.3f} ns，{1} 個 failing endpoint".format(
                whs, summary.get("ths_failing_endpoints")),
            "Hold 違規與時脈頻率無關，降頻完全無法迴避。實機上會隨溫度、電壓與晶片"
            "批次出現隨機錯誤，且通常無法穩定重現，是最不該帶上板的一種問題。",
            "檢查是否有跨時脈域路徑缺少同步器、clock skew 過大，或 I/O 的 hold "
            "constraint 有誤。修正後必須重跑 implementation。",
            blocks_bringup=True, blocks_signoff=True,
            evidence={"whs_ns": whs, "ths_ns": summary.get("ths"),
                      "failing_endpoints": summary.get("ths_failing_endpoints")},
            source="report_timing_summary"))

    wpws = summary.get("wpws")
    if wpws is not None and wpws < 0:
        findings.append(finding(
            "TIMING.PULSE_WIDTH",
            "Pulse width 違規：WPWS = {0:.3f} ns".format(wpws),
            "時脈脈寬低於該 primitive 的規格下限，元件在此條件下的行為未定義，"
            "不能假設它會正常工作。",
            "檢查 clock 的 duty cycle 與 create_clock 的 waveform 設定，"
            "以及是否有 clock 經過組合邏輯造成脈寬被壓縮。",
            blocks_bringup=True, blocks_signoff=True,
            evidence={"wpws_ns": wpws},
            source="report_timing_summary"))

    wns = summary.get("wns")
    period = _clock_period(timing)
    if wns is not None and wns < 0:
        severe = (period is not None
                  and wns < -abs(period) * thresholds["setup_severe_fraction"])
        if severe:
            findings.append(finding(
                "TIMING.SETUP_SEVERE",
                "Setup 違規幅度過大：WNS = {0:.3f} ns（週期 {1:.3f} ns）".format(
                    wns, period),
                "缺口已超過時脈週期的 {0:.0f}%。理論上仍可降頻 bring-up，但要降的"
                "幅度大到跑出來的行為已不能代表目標設計，驗證結果沒有參考價值。"
                .format(thresholds["setup_severe_fraction"] * 100),
                "先處理最差的幾條路徑（見下方 Top 10）再重跑，不建議直接上板。",
                blocks_bringup=True, blocks_signoff=True,
                evidence={"wns_ns": wns, "period_ns": period},
                source="report_timing_summary"))
        else:
            findings.append(finding(
                "TIMING.SETUP_VIOLATION",
                "Setup 違規：WNS = {0:.3f} ns，{1} 個 failing endpoint".format(
                    wns, summary.get("tns_failing_endpoints")),
                "Setup 違規與頻率成正比，降低時脈可以先上板做其他項目的 bring-up；"
                "但在原設計頻率下的行為並未經過驗證，不可據此 sign-off。",
                "若只是要先上板驗證其他功能，可降頻執行；正式驗證前仍須修正。",
                blocks_signoff=True,
                evidence={"wns_ns": wns, "tns_ns": summary.get("tns"),
                          "failing_endpoints": summary.get("tns_failing_endpoints"),
                          "period_ns": period},
                source="report_timing_summary"))

    # --- check_timing: the constraints that were never applied at all -------

    no_clock = checks.get("no_clock") or 0
    if no_clock:
        findings.append(finding(
            "TIMING.NO_CLOCK",
            "有 {0} 個 register/latch pin 沒有對應的 clock 定義".format(no_clock),
            "這些 register 完全沒有被時序分析涵蓋。報告上方好看的 WNS 並不包含"
            "它們 —— 這是最危險的情況：報告看起來是綠的，實際上有一整塊電路從未"
            "被檢查過。",
            "確認相關的 create_clock / create_generated_clock 是否存在，"
            "以及該 XDC 是否真的有被讀進專案（見上方 Input versions）。",
            blocks_bringup=True, blocks_signoff=True,
            evidence={"count": no_clock},
            source="check_timing"))

    for check_name in ("loops", "latch_loops"):
        count = checks.get(check_name) or 0
        if count:
            findings.append(finding(
                "TIMING.COMB_LOOP",
                "偵測到 {0} 個組合迴路（{1}）".format(count, check_name),
                "組合邏輯迴路的行為無法用靜態時序分析描述，實機上的表現不可預測，"
                "且會隨佈局與溫度改變。",
                "找出並移除該迴路；若是刻意的（例如 ring oscillator），"
                "必須明確加上 set_disable_timing 並在文件中記錄。",
                blocks_bringup=True, blocks_signoff=True,
                evidence={"count": count, "check": check_name},
                source="check_timing"))

    unconstrained = checks.get("unconstrained_internal_endpoints") or 0
    if unconstrained:
        total = summary.get("tns_total_endpoints")
        share = (float(unconstrained) / total) if total else None
        severe = (share is not None
                  and share > thresholds["unconstrained_severe_fraction"])
        if severe:
            findings.append(finding(
                "TIMING.UNCONSTRAINED_SEVERE",
                "有 {0} 個 endpoint 未被時序約束（佔全部的 {1:.1f}%）".format(
                    unconstrained, share * 100),
                "大量路徑沒有被分析，WNS/TNS 無法代表這個設計的真實時序。"
                "常見原因是某個 XDC 沒有被套用。",
                "先確認 constraint 是否完整套用（對照上方 Input versions 的 XDC "
                "清單），再重新評估時序結果。",
                blocks_bringup=True, blocks_signoff=True,
                evidence={"count": unconstrained, "total_endpoints": total,
                          "share": share},
                source="check_timing"))
        else:
            findings.append(finding(
                "TIMING.UNCONSTRAINED_ENDPOINTS",
                "有 {0} 個 endpoint 未被時序約束".format(unconstrained),
                "這些路徑沒有被時序分析涵蓋，屬於未知風險區域。",
                "確認這些 endpoint 是否為刻意不約束（例如未使用的除錯輸出）；"
                "若不是，補上對應的 constraint。",
                blocks_signoff=True,
                evidence={"count": unconstrained, "total_endpoints": total},
                source="check_timing"))

    io_missing = ((checks.get("no_input_delay") or 0)
                  + (checks.get("no_output_delay") or 0))
    if io_missing:
        findings.append(finding(
            "TIMING.NO_IO_DELAY",
            "有 {0} 個 port 缺少 input/output delay 約束".format(io_missing),
            "板級介面的時序完全沒有被驗證。內部 timing 再乾淨，也不保證這些訊號"
            "在實際 PCB 上與對端元件之間能正確收送。"
            "**若這些 port 正是你這次要驗證的介面，請將本項視為 BLOCKER。**",
            "對外部介面補上 set_input_delay / set_output_delay，"
            "數值依對端元件規格與板上走線延遲決定。",
            blocks_signoff=True,
            evidence={"no_input_delay": checks.get("no_input_delay"),
                      "no_output_delay": checks.get("no_output_delay")},
            source="check_timing"))

    # --- quality-of-result hints -------------------------------------------

    paths = timing.get("paths") or []
    setup_paths = [p for p in paths
                   if p.get("delay_kind") == "max" and p.get("slack_ns") is not None]
    if setup_paths:
        worst = min(setup_paths, key=lambda p: p["slack_ns"])
        route_pct = worst.get("route_pct")
        if route_pct is not None and route_pct > thresholds["route_dominated_percent"]:
            findings.append(finding(
                "TIMING.ROUTE_DOMINATED",
                "最差路徑的延遲以繞線為主（route {0:.0f}%）".format(route_pct),
                "繞線佔比偏高通常代表該區域壅塞。壅塞會讓時序結果變得不穩定，"
                "同樣的 RTL 重跑一次就可能得到明顯不同的數字。",
                "考慮降低該區域的資源使用率、檢視 floorplan，"
                "或啟用 physical optimization。",
                evidence={"route_percent": route_pct,
                          "destination": worst.get("destination")},
                source="report_timing_summary"))

        levels = worst.get("logic_levels")
        if levels is not None and levels > thresholds["deep_logic_levels"]:
            findings.append(finding(
                "TIMING.DEEP_LOGIC",
                "最差路徑的邏輯層數偏深（{0} levels）".format(levels),
                "組合邏輯層數過深會讓路徑難以達成時序，且對佈局變化很敏感。",
                "考慮在該路徑插入 pipeline stage，或重構這段組合邏輯。",
                evidence={"logic_levels": levels,
                          "destination": worst.get("destination")},
                source="report_timing_summary"))

    return findings


# ---------------------------------------------------------------------------
# CDC and clock interaction
# ---------------------------------------------------------------------------

def _evaluate_cdc(cdc):
    findings = []
    if not cdc or not cdc.get("available"):
        return findings

    grouped = {}
    for item in cdc.get("findings") or []:
        severity = (item.get("severity") or "").strip().title()
        key = (severity, item.get("id"))
        entry = grouped.setdefault(key, {"count": 0, "description": "",
                                         "clocks": set()})
        entry["count"] += 1
        entry["description"] = entry["description"] or item.get("description", "")
        if item.get("source_clock") and item.get("destination_clock"):
            entry["clocks"].add("{0} -> {1}".format(
                item["source_clock"], item["destination_clock"]))

    for (severity, identifier), entry in sorted(grouped.items()):
        description = entry["description"] or identifier
        clocks = sorted(entry["clocks"])[:6]

        if severity == "Critical":
            findings.append(finding(
                "CDC.CRITICAL",
                "{0}：{1}（{2} 處）".format(identifier, description, entry["count"]),
                "未同步或無法辨識的跨時脈域電路會造成 metastability。這正是"
                "「實驗室測起來正常、上板後偶發錯誤、且隨溫度變動」最典型的根因，"
                "用一般的功能驗證手段幾乎無法重現。",
                "為該訊號加上至少兩級同步器，並在同步器的 register 上標註 "
                "ASYNC_REG；多 bit 資料應改用 handshake 或非同步 FIFO。",
                blocks_bringup=True, blocks_signoff=True,
                evidence={"cdc_id": identifier, "count": entry["count"],
                          "clock_pairs": clocks},
                source="report_cdc"))
        elif severity == "Warning":
            findings.append(finding(
                "CDC.WARNING",
                "{0}：{1}（{2} 處）".format(identifier, description, entry["count"]),
                "跨時脈域結構存在疑慮，例如多個 bit 同時跨域。各 bit 到達的時間"
                "不一致時，接收端可能取到從未實際存在過的組合值。",
                "確認該路徑是否為單一 bit 控制訊號；若是多 bit 資料，"
                "改用 gray code、handshake 或非同步 FIFO。",
                blocks_signoff=True,
                evidence={"cdc_id": identifier, "count": entry["count"],
                          "clock_pairs": clocks},
                source="report_cdc"))

    return findings


def _evaluate_clock_interaction(interaction):
    findings = []
    if not interaction or not interaction.get("available"):
        return findings

    unsafe = []
    partial = []
    for pair in interaction.get("pairs") or []:
        classification = (pair.get("classification") or "").lower()
        label = "{0} -> {1}".format(pair["source_clock"], pair["destination_clock"])
        # "Safely Timed" must not match; "Timed (unsafe)" must.
        if "unsafe" in classification or "no common" in classification:
            unsafe.append(label)
        elif "partial false path" in classification:
            partial.append(label)

    if unsafe:
        findings.append(finding(
            "CLK.NO_COMMON_CLOCK",
            "{0} 組時脈之間沒有共同來源卻被同步分析".format(len(unsafe)),
            "兩個 clock 沒有共同的 primary clock 時，它們之間的相位關係在實機上"
            "是不確定的。Vivado 仍會算出 slack 數字，但那個數字並不成立，"
            "這類路徑實際上需要當作跨時脈域處理。",
            "若確實為非同步關係，加上 set_clock_groups -asynchronous 並補上"
            "同步器；若應為同步，檢查時脈架構是否漏了共同來源。",
            blocks_bringup=True, blocks_signoff=True,
            evidence={"pairs": unsafe[:10], "count": len(unsafe)},
            source="report_clock_interaction"))

    if partial:
        findings.append(finding(
            "CLK.PARTIAL_FALSE_PATH",
            "{0} 組時脈只有部分路徑被 false path 覆蓋".format(len(partial)),
            "exception 只蓋住了一部分路徑，剩下的仍在被分析。這種不一致通常代表"
            "constraint 寫得不完整，可能掩蓋了真正的問題。",
            "檢查對應的 set_false_path / set_clock_groups 覆蓋範圍是否完整。",
            blocks_signoff=True,
            evidence={"pairs": partial[:10], "count": len(partial)},
            source="report_clock_interaction"))

    return findings


# ---------------------------------------------------------------------------
# DRC and methodology
# ---------------------------------------------------------------------------

def _group_rules(report):
    grouped = {}
    for violation in report.get("violations") or []:
        rule = violation.get("rule")
        entry = grouped.setdefault(rule, {
            "count": 0,
            "severity": violation.get("severity"),
            "title": violation.get("title") or "",
            "detail": violation.get("detail") or "",
        })
        entry["count"] += 1
    return grouped


def _evaluate_drc(drc):
    findings = []
    if not drc or not drc.get("available"):
        return findings

    for rule, entry in sorted(_group_rules(drc).items()):
        severity = (entry["severity"] or "").strip()
        title = "{0}：{1}（{2} 處）".format(rule, entry["title"], entry["count"])
        evidence = {"rule": rule, "count": entry["count"],
                    "severity": severity, "detail": entry["detail"][:400]}

        if rule in BITSTREAM_BLOCKING_DRC:
            findings.append(finding(
                "DRC.BITSTREAM_BLOCKING", title,
                "這項規則預設會直接擋下 write_bitstream。它代表有 I/O 沒有指定 "
                "IOSTANDARD 或實體位置 —— 上板後該介面可能完全不動作，"
                "且未指定電氣標準的接腳有造成電性損傷的風險。",
                "為所有對外接腳補上 IOSTANDARD 與 PACKAGE_PIN 約束。"
                "不要用 -force 略過這項檢查。",
                blocks_bringup=True, blocks_signoff=True,
                evidence=evidence, source="report_drc"))
        elif severity == "Error":
            findings.append(finding(
                "DRC.ERROR", title,
                "DRC Error 表示設計違反了硬體上的基本規則，產生的 bitstream "
                "不應被視為有效。",
                "依 Vivado 的說明修正後重跑 implementation。",
                blocks_bringup=True, blocks_signoff=True,
                evidence=evidence, source="report_drc"))
        elif severity == "Critical Warning":
            findings.append(finding(
                "DRC.CRITICAL_WARNING", title,
                "Critical Warning 等級的 DRC 通常代表設計中存在實體實作上的問題，"
                "可能影響穩定性。",
                "逐項確認 Vivado 的說明；確認為可接受時應在專案中留下記錄。",
                blocks_signoff=True, evidence=evidence, source="report_drc"))
        elif severity == "Warning":
            findings.append(finding(
                "DRC.WARNING", title,
                "一般等級的 DRC 警告，通常不影響功能，但值得確認。",
                "視情況處理。",
                evidence=evidence, source="report_drc"))
        else:
            findings.append(finding(
                "DRC.ADVISORY", title,
                "Vivado 的建議事項。", "可選擇性處理。",
                notable=False, evidence=evidence, source="report_drc"))

    return findings


def _evaluate_methodology(methodology):
    findings = []
    if not methodology or not methodology.get("available"):
        return findings

    for rule, entry in sorted(_group_rules(methodology).items()):
        severity = (entry["severity"] or "").strip()
        title = "{0}：{1}（{2} 處）".format(rule, entry["title"], entry["count"])
        evidence = {"rule": rule, "count": entry["count"],
                    "severity": severity, "detail": entry["detail"][:400]}

        if rule in METHODOLOGY_UNSAFE_TIMING:
            findings.append(finding(
                "METH.TIMING_UNSAFE", title,
                "這是 Xilinx 自己標記為「時序分析結果不可信」的規則：相關時脈之間"
                "缺少共同來源或共同節點，算出來的 slack 並不成立。",
                "釐清時脈架構：若為非同步關係請明確宣告 set_clock_groups "
                "-asynchronous 並加同步器；若應為同步，補上共同的時脈來源。",
                blocks_bringup=True, blocks_signoff=True,
                evidence=evidence, source="report_methodology"))
        elif rule in METHODOLOGY_CLOCK_PATH:
            findings.append(finding(
                "METH.CLOCK_PATH", title,
                "時脈路徑上存在組合邏輯或 latch，會使 skew 與 jitter 不受控，"
                "時序分析的準確度下降。",
                "改用專用的 clock buffer（BUFG/BUFGCE）取代組合邏輯做時脈閘控。",
                blocks_signoff=True, evidence=evidence,
                source="report_methodology"))
        elif severity in ("Error", "Critical Warning"):
            findings.append(finding(
                "METH.CRITICAL", title,
                "方法學檢查中的 Critical 等級項目，通常代表設計或約束的寫法"
                "會影響結果的可信度。",
                "依 Vivado 的說明處理。",
                blocks_signoff=True, evidence=evidence,
                source="report_methodology"))
        elif severity == "Warning":
            findings.append(finding(
                "METH.WARNING", title,
                "方法學建議，通常與 QoR 或可維護性有關。",
                "視情況處理。", evidence=evidence, source="report_methodology"))
        else:
            findings.append(finding(
                "METH.ADVISORY", title, "Vivado 的方法學建議。", "可選擇性處理。",
                notable=False, evidence=evidence, source="report_methodology"))

    return findings


# ---------------------------------------------------------------------------
# Utilization and control sets
# ---------------------------------------------------------------------------

def _evaluate_utilization(utilization, control_sets, thresholds):
    findings = []
    if utilization and utilization.get("available"):
        for key, resource in sorted((utilization.get("resources") or {}).items()):
            percent = resource["util_percent"]
            label = "{0} 使用率 {1:.1f}%（{2}/{3}）".format(
                resource["name"], percent, resource["used"],
                resource["available"])
            evidence = {"resource": key, "util_percent": percent,
                        "used": resource["used"],
                        "available": resource["available"]}

            if percent >= thresholds["util_critical_percent"]:
                findings.append(finding(
                    "UTIL.CRITICAL", label,
                    "使用率超過 {0:.0f}% 會造成繞線壅塞，時序結果變得不可重複 —— "
                    "只改一行 RTL 就可能讓 WNS 大幅變動。這會讓後續除錯失去基準，"
                    "也代表目前的時序餘裕沒有可靠性可言。"
                    .format(thresholds["util_critical_percent"]),
                    "降低資源使用（共用邏輯、改用 BRAM 取代分散式記憶體），"
                    "或改用更大的元件。",
                    blocks_signoff=True, evidence=evidence,
                    source="report_utilization"))
            elif percent >= thresholds["util_high_percent"]:
                findings.append(finding(
                    "UTIL.HIGH", label,
                    "使用率偏高，繞線難度增加，時序結果的重複性會開始下降。",
                    "留意後續版本的成長空間。",
                    evidence=evidence, source="report_utilization"))

    if control_sets and control_sets.get("available"):
        count = control_sets.get("unique_control_sets")
        registers = None
        if utilization and utilization.get("available"):
            register = (utilization.get("resources") or {}).get("register")
            registers = register.get("used") if register else None

        if count is not None and registers:
            ratio = float(count) / registers
            if ratio > thresholds["control_sets_per_register"]:
                findings.append(finding(
                    "CTRLSET.HIGH",
                    "Control set 數量偏高：{0} 組，對應 {1} 個 register".format(
                        count, registers),
                    "control set（clock/reset/enable 的組合）過多會讓 register "
                    "難以緊密封裝進同一個 slice，推高使用率並惡化時序。",
                    "減少不必要的 reset 與 clock enable，特別是避免對每個 register "
                    "都給獨立的 enable 訊號。",
                    evidence={"unique_control_sets": count,
                              "registers": registers, "ratio": ratio},
                    source="report_control_sets"))

    return findings


# ---------------------------------------------------------------------------
# Provenance and coverage
# ---------------------------------------------------------------------------

def _evaluate_provenance(manifest, preflight):
    findings = []

    git = (manifest or {}).get("git") or {}
    dirty_inputs = git.get("dirty_inputs") or []
    if dirty_inputs:
        findings.append(finding(
            "PROV.DIRTY_INPUTS",
            "有 {0} 個設計檔未 commit，此 bitstream 無法由 git 重現".format(
                len(dirty_inputs)),
            "驗證結果無法回溯到確切的原始碼版本。之後若在板上發現問題，"
            "將無法確定當時燒錄的到底是哪一版，也無法重建相同的 bitstream。",
            "commit 這些檔案後重跑，或至少記錄下目前的檔案 hash。"
            "建議一併把 .xdc 納入版控。",
            blocks_signoff=True,
            evidence={"files": dirty_inputs[:10], "count": len(dirty_inputs)},
            source="manifest"))

    warnings = (preflight or {}).get("warnings") or []
    if warnings:
        findings.append(finding(
            "PROV.PREFLIGHT_WARNINGS",
            "Pre-flight 稽核有 {0} 項警告".format(len(warnings)),
            "專案設定存在疑慮，例如 constraint 被限定在單一階段使用，"
            "或 synthesis 與 implementation 使用了不同的 constraint fileset。",
            "檢視警告內容並修正專案設定。",
            evidence={"warnings": warnings[:10]}, source="preflight"))

    return findings


def _evaluate_coverage(reports, requested):
    """Flag analyses that did not actually run, so absence never reads as clean."""
    findings = []
    for kind in sorted(reports or {}):
        if requested is not None and kind not in requested:
            continue
        result = reports[kind] or {}
        if result.get("available"):
            continue

        safety_relevant = kind in SAFETY_RELEVANT_REPORTS
        findings.append(finding(
            "META.REPORT_UNAVAILABLE",
            "未完成的分析：{0}".format(_report_label(kind)),
            "這項分析沒有執行成功，因此本報告**沒有涵蓋**這個面向。"
            "不要把它當成「沒有問題」。" if safety_relevant else
            "這項輔助分析沒有執行成功，屬於最佳化提示，不影響安全性判定。",
            "原因：{0}。確認該報告是否有產生，或把實際的 .rpt 提供出來以調整"
            "解析格式。".format(result.get("reason", "未知")),
            blocks_signoff=safety_relevant,
            notable=safety_relevant,
            evidence={"report": kind, "reason": result.get("reason")},
            source=kind))
    return findings


def _report_label(kind):
    try:
        from vivado_reports import REPORT_LABELS
        return REPORT_LABELS.get(kind, kind)
    except ImportError:
        return kind


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def evaluate(timing=None, reports=None, manifest=None, preflight=None,
             thresholds=None, requested_reports=None):
    """Produce the full graded finding list plus the two headline verdicts."""
    merged = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        merged.update(thresholds)
    reports = reports or {}

    findings = []
    findings.extend(_evaluate_timing(timing, merged))
    findings.extend(_evaluate_cdc(reports.get("cdc")))
    findings.extend(_evaluate_clock_interaction(reports.get("clock_interaction")))
    findings.extend(_evaluate_drc(reports.get("drc")))
    findings.extend(_evaluate_methodology(reports.get("methodology")))
    findings.extend(_evaluate_utilization(reports.get("utilization"),
                                          reports.get("control_sets"), merged))
    findings.extend(_evaluate_provenance(manifest, preflight))
    findings.extend(_evaluate_coverage(reports, requested_reports))

    findings.sort(key=lambda item: (SEVERITY_ORDER.index(item["severity"]),
                                    item["id"]))
    return {"findings": findings, "verdict": summarise(findings),
            "thresholds": merged}


def summarise(findings):
    """Counts plus the bring-up / sign-off verdicts derived from them."""
    counts = dict((severity, 0) for severity in SEVERITY_ORDER)
    for item in findings:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1

    bringup_blockers = [f for f in findings if f["blocks_bringup"]]
    signoff_blockers = [f for f in findings if f["blocks_signoff"]]

    return {
        "counts": counts,
        "blockers": counts[BLOCKER],
        "criticals": counts[CRITICAL],
        "bringup_ok": not bringup_blockers,
        "signoff_ok": not signoff_blockers,
        "bringup_blocker_count": len(bringup_blockers),
        "signoff_blocker_count": len(signoff_blockers),
    }
