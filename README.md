# vivado-report-analyze-flow

在離線工作站上，把每一次 Vivado 執行的 timing report 壓縮成一份「AI agent 讀得完」的摘要，
並且在合成開始**之前**就擋掉「用到過期 RTL / 沒加進 fileset 的 XDC」這類問題。

針對的環境：

| 項目 | 版本 |
|---|---|
| OS | Red Hat Enterprise Linux 6.9（離線，無法 pip 安裝套件） |
| Vivado | 2021.2，project mode（`.xpr` + `launch_runs`） |
| AI agent | OpenCode 1.17.3 + Qwen3.6-27B（本地執行） |

---

## 解決的兩個問題

**1. Report 太大，塞爆 context window**

一份 routed 的 `report_timing_summary` 動輒上萬行。這個 flow 在本地先用 Python 解析，
只留下真正會影響判斷的內容：WNS/TNS/WHS/THS、`check_timing` 統計、clock 清單、
**最差的 10 條路徑**、以及跟上一次執行的比較。產出的 `latest_<run>.md` 大約 60~80 行。

需要看某條路徑的完整細節時，再用 `--show-path` 單獨撈出來 —— 預設精簡，需要才展開。

**2. 跑完才發現用的不是最新版本**

最常見的成因是：改了 `.xdc` 但忘記 `add_files` 加進 `constrs_1`。
這種情況 Vivado 自己的 out-of-date 偵測**不會**觸發 —— 因為那個檔案從頭到尾就不屬於這個
專案，無從偵測起。等到跑完一整輪 implementation 看到 timing 不對勁，時間已經浪費掉了。

`tcl/preflight_and_run.tcl` 在 `launch_runs` 之前先做稽核，發現問題直接中止：

- 磁碟上有 `.xdc` / RTL，但不在 fileset 裡 → **錯誤，中止**
- 專案引用的檔案在磁碟上已不存在 → **錯誤，中止**
- constraint 檔被 disable（`IS_ENABLED` = 0）→ **錯誤，中止**
- constraint 檔沒有用於 synthesis 或 implementation → 警告
- synth run 與 impl run 使用不同的 constraint fileset → 警告

同時對所有 RTL/XDC 做 sha256，跟上一次成功建置的紀錄比對。內容真的變了才 `reset_run`
強制乾淨重跑；沒變就沿用 Vivado 原本的 incremental 行為，不會每次都被迫全部重跑。

> 因為 XDC 沒有納入 git 版控，**檔案內容 hash 是唯一可靠的版本依據**，所以這裡不看 mtime
> （checkout / rsync 會動到 mtime 但內容沒變，不該觸發重跑）。RTL 的 git commit 只是額外
> 的佐證資訊。建議有空時把 `.xdc` 也納入 git，追蹤會更直接。

---

## 安裝（離線）

整包只用 Python 標準函式庫，沒有任何外部相依，直接複製到工作站即可：

```bash
scp -r vivado-report-analyze-flow user@fpga-ws:/opt/
```

### 確認 Python 3

先確認以下**任一項**可用（不需要 pip 安裝任何東西）：

```bash
# 1) 系統的 python3（RHEL 6.9 內建通常只有 python 2.6，需另外確認）
which python3 && python3 --version     # 需要 >= 3.4

# 2) Vivado 2021.2 自帶的 Python 3 —— 離線機器上一定存在
ls $XILINX_VIVADO/tps/lnx64/python-3*/bin/python3
```

`find_python` 會自動依序尋找上述兩者，通常不需要手動指定。若要指定，加上
`-python <路徑>`。

確認方式（用你實際要用的直譯器跑一次測試）：

```bash
cd /opt/vivado-report-analyze-flow
PYTHON=$XILINX_VIVADO/tps/lnx64/python-3.8.3/bin/python3 sh tests/run_tests.sh
```

---

## 使用方式

把原本手動的 `launch_runs` 換成這個單一入口：

```bash
vivado -mode batch -source /opt/vivado-report-analyze-flow/tcl/preflight_and_run.tcl -tclargs \
    -project   build/top.xpr \
    -run       impl_1 \
    -rtl-dir   rtl \
    -xdc-dir   constrs \
    -repo-root .
```

流程：稽核 fileset → 比對輸入 hash →（有變才）`reset_run` → `launch_runs` → `wait_on_run`
→ `open_run` → 產生報告 → 輸出精簡摘要。

任何一步失敗都會以非 0 狀態結束，可以直接串在 shell 腳本裡擋下後續動作。

### 常用選項

| 選項 | 說明 |
|---|---|
| `-project <xpr>` | **必要**，Vivado 專案 |
| `-run <name>` | 要建置的 run，預設 `impl_1` |
| `-rtl-dir <dir>` | 要稽核的 RTL 目錄，可重複指定 |
| `-xdc-dir <dir>` | 要稽核的 XDC 目錄，可重複指定 |
| `-outdir <dir>` | 分析輸出目錄，預設 `<xpr 所在目錄>/timing_analysis` |
| `-repo-root <dir>` | RTL 的 git working tree，用來記錄 commit |
| `-exclude <glob>` | 略過符合的檔案，可重複，例如 `-exclude "*/tb/*"` |
| `-check-only` | 只做稽核，不啟動建置（很快，適合修改後先檢查） |
| `-no-reset` | 即使輸入有變也不 `reset_run` |
| `-warn-missing-rtl` | RTL 不在 fileset 時只警告不中止 |
| `-jobs <n>` | `launch_runs` 的平行數，預設 4 |

只想快速檢查有沒有漏加檔案，不要真的跑合成：

```bash
vivado -mode batch -source .../preflight_and_run.tcl -tclargs \
    -project build/top.xpr -rtl-dir rtl -xdc-dir constrs -check-only
```

### 產出的檔案

全部放在 `<outdir>`（預設 `timing_analysis/`）：

```
timing_analysis/
  latest_impl_1.md              <- AI agent 只需要讀這個檔案
  latest_impl_1.json            <- 指向本次 run 完整紀錄的指標
  history.jsonl                 <- 每次執行一行，累積趨勢用，很小
  history/run_<時間>_impl_1.json <- 單次執行的完整結構化資料
  manifests/
    manifest_impl_1_current.json   <- 上一次成功建置的輸入 hash（基準線）
    manifest_impl_1_previous.json
    compare_impl_1.json
    preflight_impl_1.json          <- 稽核結果（錯誤與警告）
  raw/
    timing_summary_impl_1.rpt   <- 原始報告，不要餵給 AI
    utilization_impl_1.rpt
```

建議把 `timing_analysis/` 加進 FPGA 專案的 `.gitignore`。

### 深入單一路徑

摘要只列出 Top 10 路徑的重點欄位。要看某條路徑的完整 delay table：

```bash
python3 /opt/vivado-report-analyze-flow/python/analyze_run.py \
    --outdir timing_analysis --stage impl_1 \
    --show-path "accum_reg[7]"
```

會直接從原始報告中，只印出那一條路徑的完整區塊。

### 手動重新分析既有的報告

不需要重跑 Vivado，也可以對任何 `report_timing_summary` 產物重新分析：

```bash
python3 /opt/vivado-report-analyze-flow/python/analyze_run.py \
    --stage impl_1 \
    --timing-summary timing_analysis/raw/timing_summary_impl_1.rpt \
    --outdir timing_analysis
```

---

## 摘要裡有什麼

`latest_<run>.md` 的結構，依重要性排序：

1. **Input versions** —— 這次用的 RTL commit（以及是否有未 commit 的設計檔）、
   XDC 內容 digest、跟上次比是否改變、稽核結果。
   *看到 timing 異常時第一個要確認的就是這段。*
2. **Design timing summary** —— WNS/TNS/WHS/THS 與違規 endpoint 數，
   每一項都附上跟上次執行的差異與方向（better / worse）。
3. **Constraint sanity (check_timing)** —— `unconstrained_internal_endpoints`、
   `no_clock`、`no_input_delay` 等統計。這是判斷「constraint 是不是根本沒生效」
   最快的指標：數字暴增通常代表某個 XDC 沒被套用，這時 slack 數字本身沒有參考價值。
4. **Clocks** —— 各 clock 的 period / frequency。
5. **Top 10 worst setup paths** —— slack、path group、起訖點、logic levels、
   logic/route 延遲佔比（route 佔比過高通常是 placement/congestion 問題，
   logic levels 過多則是要考慮 pipeline）。
6. **Violating endpoints vs previous run** —— 新增與已解決的違規 endpoint。
   判斷「這次修改到底有沒有效」最直接的一段。
7. **Trend** —— 最近 8 次執行的 WNS/TNS/失敗數，以及各自對應的 RTL commit。

---

## 與 OpenCode / Qwen 整合

見 [`docs/opencode-integration.md`](docs/opencode-integration.md)，
內含可直接複製進專案 `AGENTS.md` 的段落。

核心原則：**agent 只讀 `latest_<run>.md`，永遠不要讀 `raw/` 底下的原始 `.rpt`。**

---

## 測試

```bash
sh tests/run_tests.sh
```

不需要 Vivado，也不需要 licence：

- **Python 單元測試** —— parser（含 `NA` 值、空報告、跨 clock group 排序）、
  manifest（hash 比對、mtime 不算變更、git porcelain 解析）、趨勢計算、CLI 行為。
- **Pre-flight 情境測試** —— `tests/vivado_stub.tcl` 模擬一個最小的 Vivado 專案物件模型
  （fileset、檔案屬性、run 生命週期），用 `tclsh` 直接驗證稽核邏輯：
  漏加 XDC / 漏加 RTL / constraint 被 disable 都必須在 `launch_runs` **之前**中止，
  而輸入沒變時不能做多餘的 `reset_run`。

---

## 尚待用真實報告驗證

`python/vivado_report_parser.py` 是依 Vivado 2021.2 的標準報告格式撰寫的，
`examples/sample_timing_summary.rpt` 是照該格式手刻的範例。

第一次在真實專案上使用後，請比對 `latest_<run>.md` 的數字與原始 `.rpt` 是否一致
（特別是 WNS/TNS 與 Top 10 路徑）。若你的專案有自訂的 report 選項導致格式不同，
把實際的 `.rpt` 片段提供出來即可據以調整 regex —— parser 各區塊是獨立的，
單一區塊格式不符不會影響其他區塊。
