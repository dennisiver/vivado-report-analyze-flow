# 八階段與指令完整參考

本檔對應 `Makefile`、`tcl/preflight_and_run.tcl` 與 `python/analyze_run.py`。

## 目錄

- [設定](#設定)
- [各階段細節](#各階段細節)
- [組合 target](#組合-target)
- [完整選項表](#完整選項表)
- [輸出檔案](#輸出檔案)
- [階段失敗時怎麼辦](#階段失敗時怎麼辦)
- [不用 Makefile](#不用-makefile)

## 設定

專案根目錄需要 `config.mk`（從 `config.mk.example` 複製）：

```make
PROJECT   = build/top.xpr     # 必填
RTL_DIRS  = rtl               # 要稽核的 RTL 目錄，可多個
XDC_DIRS  = constrs           # 要稽核的 XDC 目錄，可多個
FILELIST  = filelist.f        # 外部 file list，有才需要
OUTDIR    = timing_analysis
REPO_ROOT = .                 # RTL 的 git working tree
RUN       = impl_1
WAIVERS   = waivers.json      # 不存在就忽略
```

`VIVADO` 與 `PYTHON` 不在 PATH 上時也在這裡指定完整路徑。

## 各階段細節

### 0. `make check-env` —— 工具與環境基準線

記錄 Vivado 版本與 build、OS 發行版與核心、主機、Python，寫進
`timing_analysis/manifests/environment_current.json`，並與上次比對。

版本改變 → `ENV.VIVADO_CHANGED`（CRITICAL）。理由：換了工具版本，
先前累積的結果就不再可比 —— timing 趨勢跨版本沒有意義，IP 可能需要重新產生，
合成與佈局的演算法也不同。

OS 不在 Vivado 2024.2 支援清單 → `ENV.OS_UNSUPPORTED`（WARNING，不阻擋）。

### 1. `make check-files` —— 靜態檔案檢查

**完全不需要 Vivado，秒級完成。** 使用者改完 RTL/XDC 想快速確認時就跑這個。

解析 file list，支援三種格式：

- `.f` —— 一行一個路徑，支援 `-f <另一個清單>` 遞迴、`+incdir+`、`//` 與 `#` 註解。
  路徑相對於**該清單自己所在的目錄**（標準 `.f` 語意）。
- `.tcl` —— 抓 `add_files` / `read_verilog` / `read_vhdl` / `read_xdc` 的引數，
  會正確跳過 `-fileset sources_1` 這種選項與其值。
- `.txt` —— 一行一個路徑。

檢查項目：

1. 檔案是否存在、`+incdir+` 目錄是否存在
2. **與專案雙向對帳**（清單獨有 / 專案獨有都會報）。
   `.xpr` 直接當 XML 讀（`$PPRDIR`／`$PSRCDIR` 變數會展開），所以仍然不需要 Vivado；
   讀不到才退而使用階段 2 產生的 `manifests/filelist_<run>_sources.txt`；
   兩者都沒有就報 `FILELIST.PROJECT_NOT_CHECKED`，**不會靜靜跳過**
3. **模組層級比對** —— 掃出所有 `module`/`entity` 宣告與實例化，
   找出被用到但沒有定義的模組。這是路徑對帳擋不住的那一類問題
   （兩份清單可以完全一致，卻仍有模組沒人定義），詳見 `risk-model.md`
4. top module 是否在來源檔中找得到
5. 同名模組重複定義

`sim_1` 這類 simulation fileset 不列入 build 來源，
否則 testbench 會被誤報為「專案獨有」。

### 2. `make check-project` —— .xpr 專案稽核

`open_project` 後比對磁碟與 fileset，檢查：專案引用但已不存在的檔案、
被 disable 的 constraint（`IS_ENABLED` = 0）、`USED_IN_SYNTHESIS` /
`USED_IN_IMPLEMENTATION` 範圍、synth run 與 impl run 是否使用同一個 constraint fileset。

同時對所有 RTL/XDC 算 sha256 manifest，與上次**成功建置**的基準線比對。
基準線只在建置成功後才更新 —— 否則一次中止的執行會讓下次比對誤判為「沒變」。

用的是內容 hash 不是 mtime：checkout 或 rsync 會動 mtime 但內容沒變，
不該觸發整輪重跑。

### 3. `make elaborate` —— Elaboration 預檢

`synth_design -rtl`，只做 elaboration 不做最佳化，時間是完整合成的一小部分。

抓的是靜態檢查看不到、但要等完整合成才會浮現的錯誤：模組找不到、
port 寬度不匹配、`include` 檔遺失、語法錯誤、黑盒子。
判斷依據是這一步的 log。

### 4–5. `make synth` / `make impl`

建置後 `open_run`，產生九份報告到 `raw/`，解析後做風險評估。

報告清單：`timing_summary`、`utilization`、`drc`、`methodology`、`cdc`、
`clock_interaction`、`control_sets`、`ip_status`、`qor_assessment`。
每份各自 `catch`，某個指令在該裝置或設計狀態上不適用時只跳過該份，不影響其他。
產生前會先刪除舊檔，避免上次的結果被誤認為這次的。

同時讀 run 目錄的 `runme.log` 與 `vivado.log`。

`synth` 階段以 `stage_kind=synth` 評分（見 `risk-model.md` 的階段差異）。

### 6. `make bitstream`

`launch_runs -to_step write_bitstream`，完成後確認 `.bit` 確實存在。
要求產生但檔案不存在 → `BITSTREAM.MISSING`（BLOCKER），
通常是被 `NSTD-1`/`UCIO-1` 擋下，那兩條規則會指出實際原因。

### 7. `make signoff` —— 人類簽核報告

彙整所有階段，產生 `signoff_<時間>.md` 與 `signoff_latest.md`。

**這份是給人看的，agent 不該讀。** 內容刻意完整：完整檢查清單
（`PASS` / `FAIL` / `WAIVED` / `NOT CHECKED`）、環境與工具版本、輸入版本、
所有 BLOCKER/CRITICAL 明細與佐證 digest、已核准豁免、未檢查項目、簽核欄位。

有檢查項目 FAIL 時回傳非 0，所以 `make all` 也會據此失敗。

## 組合 target

| Target | 內容 |
|---|---|
| `make check` | 階段 0–3，所有建置前的檢查 |
| `make all` | 階段 0–7 完整流程 |
| `make help` | 列出全部 target 與目前設定 |
| `make test` | 這個工具自己的測試（不需要 Vivado） |
| `make verify-reports` | 對真實報告驗證各 parser（見下） |
| `make clean` | 清除 `OUTDIR` |

各 target 之間**刻意不設 Make 相依**：階段耗時差三個數量級，
自動連鎖觸發只會帶來意外。要照順序跑用 `make all`。

## 完整選項表

`tcl/preflight_and_run.tcl` 的 `-tclargs`：

| 選項 | 說明 |
|---|---|
| `-project <xpr>` | 必要 |
| `-run <name>` | 預設 `impl_1` |
| `-rtl-dir` / `-xdc-dir <dir>` | 可重複 |
| `-outdir <dir>` | 預設 `<xpr 目錄>/timing_analysis` |
| `-repo-root <dir>` | git working tree |
| `-exclude <glob>` | 略過檔案，可重複，例如 `*/tb/*` |
| `-reports <list>` | 要產生的輔助報告，預設全開 |
| `-waivers <file>` | 豁免檔 |
| `-check-only` | 只稽核不建置 |
| `-no-reset` | 輸入有變也不 `reset_run` |
| `-warn-missing-rtl` | RTL 不在 fileset 時只警告 |
| `-no-synth-analysis` | 不分析 synth 階段 |
| `-stop-on-synth-blocker` | synth 有 BLOCKER 就不進 implementation |
| `-write-bitstream` | 延伸到 `write_bitstream` |
| `-fail-on-blocker` | 有 BLOCKER 時非 0 結束 |
| `-jobs <n>` | 平行數，預設 4 |

**風險分級預設不影響 exit code**（build 成功就是 0）。
要用它擋下後續動作才加 `-fail-on-blocker` 或 `-stop-on-synth-blocker` ——
這樣不會突然改變既有腳本的行為。

## 輸出檔案

```
timing_analysis/
  latest_flow.md              跨階段總覽，先讀這份
  latest_<stage>.md           各階段摘要
  risk_<stage>.md / .json     各階段完整風險說明
  signoff_latest.md           人類簽核報告（agent 不讀）
  history.jsonl               每次一行，趨勢用
  history/run_<時間>_<stage>.json
  manifests/
    environment_current.json  工具版本基準線
    filelist_check.json       靜態檔案檢查結果
    manifest_<stage>_current.json
    preflight_<stage>.json
  raw/                        原始報告與 log，不要讀
```

## 階段失敗時怎麼辦

| 症狀 | 處理 |
|---|---|
| pre-flight 稽核中止 | 錯誤訊息在 stdout 與 `manifests/preflight_<run>.json`。依訊息修正專案設定，通常是漏 `add_files`。**沒有浪費合成時間，這是設計上的目的。** |
| `check-files` 說某檔不存在 | 清單路徑錯，或檔案被移動／刪除但清單沒更新 |
| `check-files` 說與 `.xpr` 不一致 | 兩份清單不同步。以其中一份為準並同步另一份 |
| elaboration 失敗 | 模組找不到或語法錯誤，詳見 `latest_elaborate.md` |
| 建置失敗 | 指向 Vivado 的 run log |
| 某份報告顯示「未檢查」 | 該報告產生失敗或格式不符。跑 `make verify-reports` 看結構指紋 |
| 缺少 `config.mk` | 訊息會直接說明要 `cp config.mk.example config.mk` |

## 不用 Makefile

Makefile 只是包裝：

```bash
vivado -mode batch -source <flow>/tcl/preflight_and_run.tcl -tclargs \
    -project build/top.xpr -run impl_1 \
    -rtl-dir rtl -xdc-dir constrs -repo-root .
```

不需要 Vivado 的部分可以直接跑 Python：

```bash
python3 <flow>/python/filelist.py --outdir timing_analysis --filelist filelist.f
python3 <flow>/python/signoff_report.py --outdir timing_analysis --stages synth_1 impl_1
python3 <flow>/python/check_reports.py --dir timing_analysis/raw
```
