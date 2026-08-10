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

SRAM_GEN_TCL = tcl/gen_sram_checkdouble2.tcl   # 你自己的 IP 產生腳本
GEN_IP_AUTO  = 0              # 1 = make check 自動補產缺的 IP
IGNORE_MODULES =              # 模組掃描要忽略的名稱
```

`VIVADO` 與 `PYTHON` 不在 PATH 上時也在這裡指定完整路徑。

## 每個 target 的結果圖示

所有 target 結束都會印一個大字 `PASS` / `WARN` / `FAIL`，同一行另有純文字的
`[PASS]` 供 grep 與 CI 使用。分級依據**不只是 exit code**：

| 圖示 | 條件 |
|---|---|
| `PASS` | exit 0，且該階段的風險判定乾淨 |
| `WARN` | exit 0 但有 WARNING/CRITICAL；或**判定檔讀不到** |
| `FAIL` | exit 非 0，**或 exit 0 但判定有 BLOCKER** |

最後一條是刻意的。這個 flow 預設在有 BLOCKER 時仍以 0 結束
（要 `-fail-on-blocker` 才擋，這樣加入風險分級不會改變既有腳本的行為），
所以照 exit code 印綠色 PASS 會在最該示警的時候說謊。

判定檔讀不到時是 `WARN` 不是 `PASS` —— 與整個 repo「沒檢查絕不呈現為沒問題」一致。

`BANNER_ASCII=1` 可強制 ASCII 字形，`NO_COLOR=1` 關閉顏色。

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

找不到定義、且名稱形如 `blk_mem_gen_<深度>x<寬度>` 的模組，尺寸會寫進
`manifests/missing_ip_specs.txt`，供 `make gen-ip` 使用（見下）。

#### 排除不在範圍內的檔案

`config.mk` 的 `EXCLUDE`（glob，可多個）**階段 1 與階段 2 共用同一個設定**。
被排除的檔案不做存在性檢查、不參與模組掃描、也不進專案對帳 ——
兩邊都不管它，三者才不會對「build 到底包含什麼」有不同答案。

樣式對**絕對路徑**比對，`*` 會跨過 `/`，與階段 2 的 `::vra::is_excluded`
（Tcl `string match`）完全一致。所以寫 `*/old_mem/*`，不是 `old_mem/*`。
刻意不支援相對路徑寫法：那會讓同一個樣式在階段 1 命中、階段 2 沒命中。

排除**永遠會被報出來**（數量、樣式、每個樣式命中幾個），
沒命中任何檔案的樣式會標記出來，簽核報告也會帶出排除數量。
排除不等於沒問題。

典型情境：file list 還留著已經廢棄的目錄，那些檔案實例化了專案裡已不存在的
IP，於是階段 1 報出一堆找不到的模組，而 Vivado GUI 卻是乾淨的
（因為專案根本沒有那些檔案）。那是**真的不同步**，不是誤報 ——
`EXCLUDE` 是給「不能改 file list」的情況用的（清單被模擬流程共用之類），
真的不需要就直接從 file list 拿掉。

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

## `make gen-ip` —— 補產缺少的 IP

階段 1 找出被實例化卻不存在的 IP，這個 target 把尺寸交給**你自己的**產生腳本
（`config.mk` 的 `SRAM_GEN_TCL`）去產。本工具不含任何 `create_ip`：
你的腳本才知道正確的 core 版本與 CONFIG 字典，這裡再寫一份只會多一種
「產出組態微妙不同的 IP」的方式。

流程是「這裡偵測 → 那裡產生 → 這裡查核」。`tcl/generate_missing_ip.tcl`
在 `source` 你的腳本前先設好 `sram_specs`，跑完後逐一 `get_ips` 確認 IP
真的存在、對應的 OOC run 沒有失敗 —— **「腳本乾淨結束」不等於「IP 產出來了」**，
只 source 不查核會讓下一階段才發現。結果寫進 `manifests/gen_ip_<時間>.json`。

腳本開頭的 spec 清單請包成守衛，單獨執行時行為完全不變：

```tcl
if {![info exists sram_specs]} {
    set sram_specs {
        2048x8
    }
}
```

**預設不掛進 `check` / `all`。** 只憑模組名稱自動產生，組態是寫死的
（單埠、無 byte write enable、輸出不註冊），可能生出名稱正確但組態錯誤的 IP ——
elaboration 會過、上板行為卻是錯的，比原本的失敗更難查。
要自動化就設 `GEN_IP_AUTO = 1`，`make check` 會在階段 1 之後補產並重檢一次。

## 組合 target

| Target | 內容 |
|---|---|
| `make check` | 階段 0–3；0–2 全部跑完一次報齊，階段 3 才 gate（見下） |
| `make all` | 階段 0–7 完整流程 |
| `make help` | 列出全部 target 與目前設定 |
| `make test` | 這個工具自己的測試（不需要 Vivado） |
| `make verify-reports` | 對真實報告驗證各 parser（見下） |
| `make clean` | 清除 `OUTDIR` |

各 target 之間**刻意不設 Make 相依**：階段耗時差三個數量級，
自動連鎖觸發只會帶來意外。要照順序跑用 `make all`。

### `make check` 的 fail-fast 粒度

不是「第一個失敗就停」，而是**便宜的全跑完、昂貴的才 gate**：

1. 階段 0、1、2 依序執行，**全部跑完**並收集結果。三者都是秒級，
   第一個失敗就停只會讓人為了 N 個問題來回 N 次
2. 某階段失敗導致下一階段真的不可能執行時（Vivado 不可用 → 開不了專案），
   標成「未執行」而**不是靜靜跳過**
3. 階段 3 是分鐘級，只在 0–2 沒有 BLOCKER 時才跑

gate 的條件是**風險判定**不是 exit code —— 各階段刻意在有 BLOCKER 時仍回 0，
照 exit code 判斷會讓一個已知缺模組的設計白花五分鐘證明它缺模組。
`make check` 這條路徑會另外帶 `-fail-on-blocker` 給階段 3：
手動跑單一階段維持寬鬆，串成 pre-check 時嚴格。

彙總寫在 `precheck_latest.md`，不進 elaboration 的原因也在裡面。
邏輯在 `scripts/precheck.sh` 與 `python/precheck.py`。

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
  precheck_latest.md          建置前檢查彙總（只跑 make check 時讀這份）
  precheck.json               同上的結構化版本，含 elaborate_allowed
  latest_<stage>.md           各階段摘要
  risk_<stage>.md / .json     各階段完整風險說明
  risk_env.json               階段 0 的判定（供結果圖示判讀）
  risk_files.json             階段 1 的判定
  signoff_latest.md           人類簽核報告（agent 不讀）
  history.jsonl               每次一行，趨勢用
  history/run_<時間>_<stage>.json
  manifests/
    environment_current.json  工具版本基準線
    filelist_check.json       靜態檔案檢查結果
    missing_ip_specs.txt      缺少的 IP 尺寸，供 make gen-ip
    gen_ip_<時間>.json        補產結果與事後查核
    manifest_<stage>_current.json
    preflight_<stage>.json
  raw/                        原始報告與 log，不要讀
```

階段 0 與階段 1 自己寫 `risk_env.json` / `risk_files.json`：這兩個階段的問題
反映不到 exit code（版本改變、模組找不到都不會讓指令失敗），沒有判定檔的話
結果圖示只能照 exit code 印綠燈。階段 2 不需要 —— 它的稽核失敗本來就會非 0 結束。

## 階段失敗時怎麼辦

| 症狀 | 處理 |
|---|---|
| pre-flight 稽核中止 | 錯誤訊息在 stdout 與 `manifests/preflight_<run>.json`。依訊息修正專案設定，通常是漏 `add_files`。**沒有浪費合成時間，這是設計上的目的。** |
| `check-files` 說某檔不存在 | 清單路徑錯，或檔案被移動／刪除但清單沒更新 |
| `check-files` 說與 `.xpr` 不一致 | 兩份清單不同步。以其中一份為準並同步另一份 |
| `check-files` 說某個 IP 缺少 | 設好 `SRAM_GEN_TCL` 後跑 `make gen-ip`；不是 IP 的話用 `IGNORE_MODULES` 排除 |
| `make check` 沒有進 elaboration | 原因列在 `precheck_latest.md` 開頭。這是設計行為，不是壞掉 |
| `gen-ip` 說 IP「仍然不存在」 | 產生腳本乾淨結束但沒產出，通常是它的 spec 清單沒涵蓋那個尺寸，或守衛沒加所以用了自己的硬編清單 |
| banner 顯示 `FAIL` 但指令成功 | 正常。風險判定有 BLOCKER，exit code 預設不擋 |
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
python3 <flow>/python/precheck.py --outdir timing_analysis --run impl_1 \
    --status env=0 --status files=0 --status project=0
python3 <flow>/python/signoff_report.py --outdir timing_analysis --stages synth_1 impl_1
python3 <flow>/python/check_reports.py --dir timing_analysis/raw
python3 <flow>/python/banner.py --status $? --label "自訂步驟"
```
