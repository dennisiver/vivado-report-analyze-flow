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

每次 Vivado 建置都是透過 `<FLOW>/tcl/preflight_and_run.tcl` 執行的，
結果已經自動整理好，放在 `timing_analysis/` 底下。

### 讀取規則

- Timing 相關的問題，**一律先讀 `timing_analysis/latest_<run>.md`**
  （例如 `timing_analysis/latest_impl_1.md`）。這份摘要包含：
  本次使用的 RTL commit 與 XDC digest、WNS/TNS/WHS/THS 及與上次的差異、
  `check_timing` 統計、clock 清單、最差的 10 條路徑、以及趨勢表。

- **絕對不要讀取 `timing_analysis/raw/` 底下的 `.rpt` 檔案。**
  那是原始報告，有上萬行，讀進來會塞爆 context window。
  摘要裡已經有你需要的所有彙總資訊。

- 需要某條路徑的完整 delay table 時，才執行：

  ```bash
  python3 <FLOW>/python/analyze_run.py \
      --outdir timing_analysis --stage impl_1 --show-path "<endpoint 名稱片段>"
  ```

  這只會印出那一條路徑的完整區塊。一次只查一條，不要一次查很多條。

- 需要歷史趨勢時讀 `timing_analysis/history.jsonl`，每次執行一行，檔案很小。

### 判讀順序

1. 先看摘要最上方的 **Input versions**。如果 XDC digest 跟預期不符、
   或顯示有未 commit 的設計檔，先向使用者確認輸入版本是否正確，
   再去分析 slack 數字 —— 否則可能是在分析一份用錯輸入跑出來的結果。

2. 再看 **Constraint sanity (check_timing)**。
   `unconstrained_internal_endpoints` 很大或 `no_clock` 不為 0，
   通常代表某個 constraint 根本沒被套用。這種情況下 WNS/TNS 沒有參考價值，
   應該先解決 constraint 問題，而不是去改 RTL。

3. 確認上述兩點都正常後，才開始分析 WNS/TNS 與個別路徑。

### 分析路徑時的常見判斷

- **route 佔比過高**（例如 route > 70%）→ placement / congestion 問題，
  考慮 floorplan、physical optimization、或降低該區域的使用率。
- **logic levels 過多** → 邏輯層數太深，考慮插 pipeline stage 或重構該段組合邏輯。
- **同一個模組反覆出現在 Top 10** → 該模組是瓶頸，優先處理。
- **跨 clock domain 的路徑** → 先確認是否應該加 `set_false_path` 或
  `set_max_delay -datapath_only`，而不是硬做 timing closure。

### 如果 pre-flight 中止了

`preflight_and_run.tcl` 若在稽核階段中止（例如某個 `.xdc` 沒加進 fileset），
不會有新的 timing 摘要產生。此時錯誤訊息已經寫在
`timing_analysis/manifests/preflight_<run>.json` 以及 Vivado 的 log 中，
直接依訊息修正專案設定即可，不需要去翻 timing 報告。
```

## 提醒使用者的注意事項

- 摘要檔會**每次覆寫**。如果需要保留某次的結果，`timing_analysis/history/` 底下
  有依時間戳記命名的完整 JSON，不會被覆寫。
- `latest_<run>.md` 裡的 run 名稱對應 Vivado 的 run（`impl_1`、`synth_1`），
  同一個專案的不同 run 會各自有一份摘要與各自的趨勢紀錄。
