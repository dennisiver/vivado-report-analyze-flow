---
name: vivado-fpga-flow
description: >-
  Run and interpret the staged Vivado FPGA synthesis checking flow
  (vivado-report-analyze-flow): eight stages from file-list checks through
  synthesis, implementation and a human sign-off report, grading every finding
  as BLOCKER / CRITICAL / WARNING / INFO against two separate verdicts — safe to
  bring up on hardware, and safe to sign off. Use this skill whenever the user is
  working on a Vivado or FPGA project and mentions timing closure, WNS, TNS,
  slack, setup or hold violations, CDC, clock domain crossing, DRC, methodology
  checks, utilization, congestion, IP status, bitstream generation or sign-off —
  including in Chinese ("能不能上板"、"timing 過不過"、"這版可以交嗎"), and also when a
  build may have used stale inputs (a file missing from the file list, an .xdc
  never added to constrs_1, a changed Vivado version). Prefer this skill over
  opening Vivado reports directly: the raw reports run to tens of thousands of
  lines and this flow has already reduced them.
---

# Vivado FPGA 合成檢查流程

這個 flow 把 Vivado 的合成結果整理成分級過的風險判定，並在合成**開始之前**就攔下
「用到過期輸入」這類問題。你的工作是執行對應的階段、讀正確的摘要檔、
把結果翻譯成使用者能行動的建議。

## 先確認前提

這個 skill 需要 flow 已安裝，且專案裡有 `config.mk`。開始之前先確認：

```bash
ls config.mk timing_analysis/ 2>/dev/null
```

- 兩者都在 → 直接往下做。
- 沒有 `config.mk` → 這個專案還沒設定過。請使用者執行
  `cp <flow 安裝路徑>/config.mk.example config.mk` 並填入 `PROJECT`、`RTL_DIRS`、
  `XDC_DIRS`、`FILELIST`，不要自己猜路徑填進去。
- 有 `config.mk` 但沒有 `timing_analysis/` → 還沒跑過任何階段，先執行
  `make check-files`（秒級，不需要 Vivado）。

## 讀取規則

這是整個 flow 的核心設計，也是最容易做錯的地方。輸出刻意分成好幾層，
**由粗到細**，你應該從最粗的一層開始，需要細節才往下走：

| 檔案 | 什麼時候讀 |
|---|---|
| `timing_analysis/latest_flow.md` | **一律先讀這份。** 跨階段總覽，約 30 行，含兩個判定、各階段的 BLOCKER/CRITICAL 數量、合併後的 BLOCKER 清單、**未執行的階段** |
| `timing_analysis/precheck_latest.md` | 只跑了 `make check`（還沒建置）時讀這份。階段 0-2 的彙總，以及**為什麼沒有進 elaboration** |
| `timing_analysis/latest_<stage>.md` | 需要某個階段的細節時（`latest_impl_1.md`、`latest_synth_1.md`） |
| `timing_analysis/risk_<stage>.md` | 需要 CRITICAL / WARNING 等級的完整說明時 |

**不要讀這兩類檔案：**

- `timing_analysis/raw/` 底下的 `.rpt` 與 `.log` —— 那是原始輸出，動輒上萬行。
  讀進來會塞爆 context，而且摘要裡已經有全部的彙總資訊。
  需要某條路徑的完整 delay table 時，用下面的 drill-down 指令單獨取一條。
- `signoff_*.md` —— 那是給**人**審閱簽核用的完整報告，刻意冗長。
  內容你在上面三份檔案裡都已經有了。使用者要簽核報告時請他跑 `make signoff`。

需要單一路徑的完整細節時：

```bash
python3 <flow>/python/analyze_run.py --outdir timing_analysis \
    --stage impl_1 --show-path "<endpoint 名稱片段>"
```

一次查一條。這個設計的用意就是「預設精簡，需要才展開」。

## 八個階段

每個階段都能單獨執行。順序是刻意的：**越便宜的檢查越早做**。

| 階段 | 指令 | 檢查什麼 | 需要 Vivado |
|---|---|---|---|
| 0 | `make check-env` | Vivado 版本、OS、Python，與上次比對 | 是 |
| 1 | `make check-files` | file list 的檔案是否存在、與 `.xpr` 雙向對帳 | **否**（秒級） |
| 2 | `make check-project` | fileset 稽核、constraint 是否被套用 | 是（秒級） |
| 3 | `make elaborate` | 模組找不到、port 不匹配、語法錯誤 | 是（分鐘級） |
| — | `make gen-ip` | 用使用者自己的腳本補產階段 1 找到的缺 IP | 是（分鐘級，需明確要求） |
| 4 | `make synth` | 合成 + 分析 | 是 |
| 5 | `make impl` | 實作 + 完整分析 | 是 |
| 6 | `make bitstream` | 產生 `.bit` 並確認存在 | 是 |
| 7 | `make signoff` | 人類簽核報告 | **否** |

組合：`make check`（0–3）、`make all`（0–7）、`make help`。

`make check` 不是單純把四個階段串起來：階段 0–2 都是秒級，會**全部跑完一次報齊**
（第一個失敗就停，只會讓人為了 N 個問題來回 N 次）；階段 3 是分鐘級，
才依彙總後的風險判定決定跑不跑。結論寫在 `precheck_latest.md`。

使用者改了 RTL 或 XDC 之後想快速確認，建議 `make check-files` ——
不需要 Vivado，幾秒鐘就知道有沒有漏檔。

**每個 target 結束都會印一個大字結果**：`PASS` / `WARN` / `FAIL`，同一行也有
純文字的 `[PASS]` 可以 grep。注意 `FAIL` 不只看 exit code —— 這個 flow 刻意在
有 BLOCKER 時仍以 0 結束（要 `-fail-on-blocker` 才擋），所以 banner 會讀該階段的
風險判定。看到 `[FAIL]` 但指令「成功」了，那是正常的，代表判定說不可上板。

`make gen-ip`：階段 1 發現被實例化卻不存在、名稱長得像 `blk_mem_gen_<深度>x<寬度>`
的模組時，把尺寸交給使用者自己的 IP 產生腳本（`config.mk` 的 `SRAM_GEN_TCL`）補產。
**這個 target 不會自動執行**，因為只憑模組名稱產生的 IP 組態是寫死的，
有機會生出「名稱對、組態錯」的 core。要建議使用者跑，不要自己假設可以跑。

## 核心心智模型：兩個判定

每一項發現都用兩個**獨立**的問題描述，因為在 FPGA 上這兩件事的答案常常不同：

- **是否阻擋上板 bring-up** —— 現在拿這個 bitstream 進實驗室，得到的結論可不可信？
- **是否阻擋 sign-off** —— 這樣可不可以正式交付？

嚴重度是從這兩者推導出來的，不是另外標的：

| 阻擋 bring-up | 阻擋 sign-off | 嚴重度 |
|---|---|---|
| 是 | 是 | **BLOCKER** |
| 否 | 是 | **CRITICAL** |
| 否 | 否（但值得注意） | WARNING |
| 否 | 否 | INFO |

最能說明這個模型的例子：

- **Setup 違規**（`TIMING.SETUP_VIOLATION`）→ CRITICAL。
  降低時脈就能先上板做其他項目的 bring-up，但原頻率下的行為未經驗證。
- **Hold 違規**（`TIMING.HOLD_VIOLATION`）→ BLOCKER。
  hold 與頻率無關，降頻完全無效；實機會隨溫度、電壓、晶片批次隨機出錯。

使用者問「現在能不能先上板測」時看第一個判定；問「這版可不可以交」時看第二個。

## 回答問題的順序

被問到「這版有沒有問題 / 能不能上板」時：

1. **先看未執行的階段。** `latest_flow.md` 會列出來。若使用者問的面向對應到沒跑過的
   階段，先說明那部分**尚未檢查**，並建議對應的 `make` target ——
   不要用其他階段的結果去推測。「沒檢查」和「檢查過沒問題」是完全不同的兩件事。

2. **有 BLOCKER 就先講 BLOCKER。** 直接列出來並說明為什麼必須先解決。
   在還有 BLOCKER 的情況下把重點放在微調 WNS 是本末倒置。

3. **確認輸入版本。** 摘要裡有 RTL commit 與 XDC digest。
   如果顯示有未 commit 的設計檔，或 digest 與預期不符，
   先向使用者確認輸入是否正確 —— 否則可能是在分析一份用錯輸入跑出來的結果。

4. **看 constraint 是否真的生效。** `unconstrained_internal_endpoints` 很大、
   `no_clock` 不為 0，或出現 `LOG.CONSTRAINT_NOT_APPLIED`，
   都代表某些約束根本沒套用。這種情況下 WNS/TNS 沒有參考價值，
   該先解決 constraint，而不是去改 RTL。

5. 以上都正常，才開始分析個別路徑與時序數字。

## 兩個容易誤判的地方

**階段不同，同一個數字意義不同。** 合成後（`synth_1`）的時序是**尚未佈局的估算值**，
負的 WNS 很常見而且經常被 implementation 修掉，所以只評為 WARNING。
不要在 synth 階段就建議使用者改 RTL 追時序。但**約束類問題在兩個階段同級**，
因為那些合成後就已經確定，place & route 不會改變它們。

**「未檢查」不等於「沒問題」。** 某份報告解析失敗或某階段沒跑，會顯示為
`META.REPORT_UNAVAILABLE` 或列在「未檢查的項目」。
不要因為報告沒列出 CDC 問題就說 CDC 沒問題 —— 要先確認 CDC 真的被檢查過。

## 已豁免的項目

標記為「已豁免」的項目代表有人審查並接受了那個風險，不要重複建議修正，
除非使用者主動問起。若顯示某個豁免**已失效**，那是因為該項目的實際內容改變了
（違規數量增加、換成別的 instance），原本的審查已不涵蓋現況 ——
這種情況要主動提醒使用者重新審查。

## 需要更多細節時

本檔到此已涵蓋日常使用。以下三份放在 `references/`，**有需要才讀**：

- `references/risk-model.md` —— 完整規則表（全部 57 條 rule ID）、各規則的兩個旗標、
  階段差異、log 訊息分類、閾值、waiver 格式。
  **要判斷某個具體 rule ID 的意義時讀這份。**
- `references/stages-and-commands.md` —— 八階段的完整細節、所有指令選項、
  各階段失敗時的處理、輸出檔案完整清單。
  **要執行流程、調整選項、或某階段失敗時讀這份。**
- `references/fpga-interpretation.md` —— FPGA 領域判讀：route 佔比、logic levels、
  CDC 同步器、I/O 約束、壅塞、utilization 的實務建議。
  **要給出「怎麼修」的具體建議時讀這份。**
