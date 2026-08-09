# 與 OpenCode / Qwen 整合

這個 flow 刻意設計成**獨立的 CLI pipeline**，不需要改 OpenCode 的 tool 或 MCP 設定。
Vivado 跑完後摘要就已經寫在磁碟上，agent 只要知道「該讀哪個檔案、不該讀哪個檔案」即可。

## 為什麼是這種做法

Qwen3.6-27B 在本地執行，context window 有限。原始的 `report_timing_summary` 動輒上萬行，
一旦讀進去就沒有空間留給實際的分析與對話了。

所以分工是：

- **Python 負責資料縮減** —— 精確的數字解析、hash 比對、趨勢計算，這些都是確定性的工作，
  交給程式做既省 token 又不會出錯。
- **LLM 負責判讀** —— 拿到已經整理好的數字後，判斷問題模式、提出修改建議。

## 設定方式

把下面這段複製進 FPGA 專案根目錄的 `AGENTS.md`（OpenCode 會自動讀取），
並把 `<FLOW>` 換成實際安裝路徑（例如 `/opt/vivado-report-analyze-flow`）：

```markdown
## Vivado Timing 分析

每次 Vivado 建置都是透過 `make -f <FLOW>/Makefile` 執行的（八個階段），
結果已經自動整理好，放在 `timing_analysis/` 底下。

可用的階段（使用者可以單獨執行任一個）：
`make check-env`（工具版本）、`make check-files`（靜態檔案檢查，不需要 Vivado）、
`make check-project`（.xpr 稽核）、`make elaborate`（elaboration 預檢）、
`make synth`、`make impl`、`make bitstream`、`make signoff`（人類簽核報告）。

### 讀取規則

- 任何「這版能不能上板 / 有什麼問題」的問題，**先讀
  `timing_analysis/latest_flow.md`**。這是跨階段總覽（約 30 行），
  包含兩個判定、各階段的 BLOCKER/CRITICAL 數量、合併後的 BLOCKER 清單，
  以及**未執行的階段**。

- 需要某個階段的細節時，再讀 `timing_analysis/latest_<stage>.md`
  （例如 `latest_impl_1.md`、`latest_synth_1.md`）。
  內容包含該階段的風險判定與 BLOCKER、本次使用的 RTL commit 與 XDC digest、
  WNS/TNS/WHS/THS 及與上次的差異、`check_timing` 統計、最差的 10 條路徑、趨勢表。

- 需要 CRITICAL / WARNING 等級的完整說明時，才讀 `timing_analysis/risk_<stage>.md`。

- **絕對不要讀取 `timing_analysis/raw/` 底下的 `.rpt` 或 `.log`。**
  那是原始報告與 log，有上萬行，讀進來會塞爆 context window。
  摘要裡已經有你需要的所有彙總資訊。

- **也不要讀 `signoff_*.md`。** 那是給人類審閱簽核用的完整報告，刻意冗長，
  內容你在上面幾份檔案裡都已經有了。使用者若要簽核報告，
  請他執行 `make signoff` 而不是把內容貼給你。

- 需要某條路徑的完整 delay table 時，才執行：

  ```bash
  python3 <FLOW>/python/analyze_run.py \
      --outdir timing_analysis --stage impl_1 --show-path "<endpoint 名稱片段>"
  ```

  這只會印出那一條路徑的完整區塊。一次只查一條，不要一次查很多條。

- 需要歷史趨勢時讀 `timing_analysis/history.jsonl`，每次執行一行，檔案很小。

### 判讀順序

0. **先確認階段涵蓋範圍**。`latest_flow.md` 會列出未執行的階段。
   若使用者問的面向對應到沒跑過的階段，先說明那部分尚未檢查，
   並建議他執行對應的 `make` target，而不是憑其他階段的結果推測。

1. 再看 **驗證風險判定**。
   - 有 BLOCKER 時，直接回報這些項目，並說明必須先解決才能上板。
     **不要**在還有 BLOCKER 的情況下，把重點放在微調 WNS 上。
   - 注意「可否上板 bring-up」與「可否 sign-off」是兩個不同的判定。
     使用者問「現在能不能先上板測」時看前者；問「這版可不可以交」時看後者。
   - 若列出了**未檢查的項目**，要主動提醒使用者：那個面向沒有被涵蓋，
     不代表沒有問題。不要因為報告沒列出 CDC 問題就說 CDC 沒問題。

2. 再看 **Input versions**。如果 XDC digest 跟預期不符、或顯示有未 commit 的設計檔，
   先向使用者確認輸入版本是否正確，再去分析 slack 數字 ——
   否則可能是在分析一份用錯輸入跑出來的結果。

3. 再看 **Constraint sanity (check_timing)**。
   `unconstrained_internal_endpoints` 很大或 `no_clock` 不為 0，
   通常代表某個 constraint 根本沒被套用。這種情況下 WNS/TNS 沒有參考價值，
   應該先解決 constraint 問題，而不是去改 RTL。

4. 確認上述都正常後，才開始分析 WNS/TNS 與個別路徑。

### 階段差異：同一個數字，意義不同

合成後（`synth_1`）的時序是**尚未佈局的估算值**。負的 WNS 在這個階段很常見，
而且經常被 implementation 修掉，所以只會被評為 WARNING。
**不要在 synth 階段就建議使用者去改 RTL 追時序**，除非缺口大到被評為 CRITICAL。

但**約束類問題（`no_clock`、未約束 endpoint、constraint 沒套用）在兩個階段同級**，
因為那些在合成後就已經確定，place & route 不會改變它們 —— 看到就該立刻處理。

Hold 違規在 synth 階段**不會出現**（還沒繞線，數字沒有意義）；
在 impl 階段則是 BLOCKER。

### 嚴重度的意義

嚴重度是由兩個判定推導出來的，回答使用者時請沿用這個區分：

- **BLOCKER** —— 阻擋上板也阻擋 sign-off。例如 hold 違規：與時脈頻率無關，
  降頻無法迴避，帶上板得到的任何結論都不可信。
- **CRITICAL** —— 只阻擋 sign-off。例如 setup 違規：可以降頻先做 bring-up，
  但原頻率下的行為未經驗證。
- **WARNING** —— 影響穩定性或結果的可重複性（例如使用率過高造成壅塞）。
- **INFO** —— 觀察項。

### 分析路徑時的常見判斷

- **route 佔比過高**（例如 route > 70%）→ placement / congestion 問題，
  考慮 floorplan、physical optimization、或降低該區域的使用率。
- **logic levels 過多** → 邏輯層數太深，考慮插 pipeline stage 或重構該段組合邏輯。
- **同一個模組反覆出現在 Top 10** → 該模組是瓶頸，優先處理。
- **跨 clock domain 的路徑** → 先確認是否應該加 `set_false_path` 或
  `set_max_delay -datapath_only`，而不是硬做 timing closure。
  但要注意：加 exception 只是讓 timing 報告不再分析它，
  **並沒有解決 metastability** —— 該加的同步器還是要加。

### 已豁免的項目

摘要中標記為「已豁免」的項目，代表已經有人審查並接受了那個風險。
**不要重複建議修正已豁免的項目**，除非使用者主動問起。

若報告顯示某個豁免「已失效」，那是因為該項目的實際內容改變了
（違規數量增加、換成別的 instance），原本的審查已不再涵蓋現況 ——
這種情況要主動提醒使用者重新審查。

### 如果 pre-flight 中止了

`preflight_and_run.tcl` 若在稽核階段中止（例如某個 `.xdc` 沒加進 fileset），
不會有新的 timing 摘要產生。此時錯誤訊息已經寫在
`timing_analysis/manifests/preflight_<run>.json` 以及 Vivado 的 log 中，
直接依訊息修正專案設定即可，不需要去翻 timing 報告。
```

## 提醒使用者的注意事項

- 摘要檔會**每次覆寫**。如果需要保留某次的結果，`timing_analysis/history/` 底下
  有依時間戳記命名的完整 JSON，`signoff_<時間>.md` 也不會被覆寫。
- 使用者若還沒跑過某個階段，agent 應該建議對應的 `make` target，
  而不是從其他階段的結果推測。
- `latest_<run>.md` 裡的 run 名稱對應 Vivado 的 run（`impl_1`、`synth_1`），
  同一個專案的不同 run 會各自有一份摘要與各自的趨勢紀錄。
