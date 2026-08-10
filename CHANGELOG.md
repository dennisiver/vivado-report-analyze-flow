# Change log

給沒有 git 的離線工作站對照用。最新的在最上面。

要確認手上那份是哪個版本，跑：

```bash
sh scripts/checksums.sh
```

把輸出跟本檔最後的〈檔案指紋〉比對，或在兩份 copy 上各跑一次再 `diff`。

---

## dcb9b92 —— 風險判定 gate、結果圖示、補產缺少的 IP

起因：`SRAM_1216x80.v` 在 `` `ifdef VIVADO `` 下實例化 `blk_mem_gen_1216x80`，
該 IP 不在專案中，一路到 elaboration（~5 分鐘）才失敗。

### 新增檔案

| 檔案 | 用途 |
|---|---|
| `python/banner.py` | 每個 target 結束的大字 PASS/WARN/FAIL |
| `python/precheck.py` | 彙總階段 0-2、決定階段 3 跑不跑 |
| `scripts/with_banner.sh` | 跑指令 → 印圖示 → **原樣傳遞 exit code** |
| `scripts/precheck.sh` | `make check` 的驅動邏輯 |
| `scripts/checksums.sh` | 無 git 環境的版本對照工具 |
| `tcl/generate_missing_ip.tcl` | 把缺的 IP 尺寸餵給**你自己的**產生腳本，跑完查核 |
| `tests/test_banner.py` | 17 個測試 |
| `tests/test_precheck.py` | 12 個測試 |
| `tests/test_gen_ip.tcl` | 4 個情境 |

**新增目錄 `scripts/`** —— 之前沒有這個目錄，複製時別漏掉。

### 修改檔案

| 檔案 | 改了什麼 |
|---|---|
| `Makefile` | 所有 target 包上 banner；`check` 改寫為 pre-check 驅動；新增 `gen-ip`；加 `.NOTPARALLEL`；sub-make 補上 `-f`（見下方「修掉的 bug」）；`SRAM_GEN_TCL` / `GEN_IP_AUTO` / `ELABORATE_ARGS` |
| `python/filelist.py` | 新增 `sram_specs()`；寫出 `manifests/missing_ip_specs.txt` 與 `risk_files.json`；報告多一段 `ip :` |
| `python/environment.py` | 新增 `write_assessment()`，寫出 `risk_env.json` |
| `config.mk.example` | `SRAM_GEN_TCL`、`GEN_IP_AUTO` 說明 |
| `tests/vivado_stub.tcl` | 新增 `get_ips` / `create_ip` / `create_ip_run` / `set_property` / `generate_target` / `export_ip_user_files` / `close_project` |
| `tests/run_tests.sh` | 新增 banner、`make check`、gen-ip 的情境測試 |
| `tests/test_skill.py` | 新增「SKILL.md 的規則總數必須與程式一致」的測試 |
| `SKILL.md`、`stages-and-commands.md`、`README.md`、`docs/skill-authoring.md` | 文件同步 |

### 行為變化

**1. 每個 target 結束會印大字 `PASS` / `WARN` / `FAIL`**

同一行另有純文字 `[PASS]` 供 grep 與 CI 使用。

分級**不只看 exit code**：

| 圖示 | 條件 |
|---|---|
| `PASS` | exit 0，且該階段的風險判定乾淨 |
| `WARN` | exit 0 但有 WARNING/CRITICAL；或**判定檔讀不到** |
| `FAIL` | exit 非 0，**或 exit 0 但判定有 BLOCKER** |

最後一條是刻意的：這個 flow 預設在有 BLOCKER 時仍以 0 結束（要
`-fail-on-blocker` 才擋，這樣加入風險分級不會改變既有腳本的行為），
照 exit code 印綠燈會在最該示警的時候說謊。
**看到 `[FAIL]` 但指令「成功」是正常的。**

`BANNER_ASCII=1` 強制 ASCII 字形，`NO_COLOR=1` 關顏色。

**2. 階段 0 和 1 開始寫自己的判定檔**

`timing_analysis/risk_env.json`、`timing_analysis/risk_files.json`。
這兩階段的問題（版本改變、模組找不到）到不了 exit code，沒有判定檔的話
圖示只能照 exit code 印綠燈。階段 2 不需要 —— 它的稽核失敗本來就非 0 結束。

**3. `make check` 的 fail-fast 改成有粒度**

以前：任一階段回非 0 就停。
現在：階段 0、1、2 **全部跑完一次把問題報齊**（三者都是秒級，第一個失敗就停
只會讓人為了 N 個問題來回 N 次）；階段 3 是分鐘級，才依**彙總後的風險判定**
（不是 exit code）決定跑不跑。

某階段失敗導致下一階段真的不可能執行時（Vivado 不可用 → 開不了專案），
標成「未執行」而不是靜靜跳過。

新輸出：`timing_analysis/precheck_latest.md`（人／agent 讀這份）與
`precheck.json`。`make check` 這條路徑會多帶 `-fail-on-blocker` 給階段 3。

**四個獨立 target 的行為完全沒變**，手動跑單一階段維持寬鬆。

**4. 新增 `make gen-ip`**

階段 1 掃到被實例化卻不存在、名稱形如 `blk_mem_gen_<深度>x<寬度>` 的模組時，
把尺寸寫進 `timing_analysis/manifests/missing_ip_specs.txt`。
`make gen-ip` 把那份清單交給 `config.mk` 裡 `SRAM_GEN_TCL` 指定的**你自己的**
腳本，跑完再逐一 `get_ips` 查核 —— **「腳本乾淨結束」不等於「IP 產出來了」**。
結果寫進 `manifests/gen_ip_<時間>.json`。

本工具刻意不含任何 `create_ip`。

**預設不掛進 `check` / `all`**：只憑模組名稱產生的 IP 組態是寫死的
（單埠、無 byte write enable、輸出不註冊），可能生出名稱正確但**組態錯誤**的
core —— elaboration 會過、上板卻是錯的，比原本的失敗更難查。
要自動化設 `GEN_IP_AUTO = 1`。

### 要你動手的兩件事

**(a) `config.mk` 加兩行**（`config.mk` 不會被覆蓋，要自己加）：

```make
SRAM_GEN_TCL = tcl/gen_sram_checkdouble2.tcl
GEN_IP_AUTO  = 0
```

**(b) `gen_sram_checkdouble2.tcl` 開頭加三行守衛**：

```tcl
if {![info exists sram_specs]} {
    set sram_specs {
        2048x8
    }
}
```

原本是直接 `set sram_specs { 2048x8 }`。加了守衛之後**單獨執行行為完全不變**，
被 `generate_missing_ip.tcl` `source` 時才改用偵測到的清單。
以下的產生邏輯一個字都不用動。

不想改也可以：`make gen-ip` 仍照原樣執行硬編清單，
`make check-files` 會印出要加進 `sram_specs` 的尺寸。

### 修掉的 bug

`scripts/precheck.sh` 裡的 sub-make 原本沒帶 `-f`，用
`make -f <flow>/Makefile check` 會讓每個階段都變成
`No rule to make target 'check-env'`，gate 因此看不到任何結果。
已修並加了回歸測試。

### 順手修正

`SKILL.md` 寫「全部 47 條 rule ID」，實際 57 條。已修正，
並加了會機器檢查這個數字的測試（原本只檢查 `risk-model.md` 的 BLOCKER 數）。

### 測試

271 個 Python 測試 + 7 個 Makefile 情境 + 11 個 pre-flight + 4 個 gen-ip，
全部通過。`make test`（不需要 Vivado）。

---

## aca652d —— check-files 與專案對帳、抓出找不到的模組

**如果你手上那份沒有 `examples/sample_project.xpr`，就是還沒有這個版本。**

### 新增檔案

| 檔案 | 用途 |
|---|---|
| `examples/sample_project.xpr` | 手刻的 .xpr 範例，供測試與格式對照 |

### 修改檔案

`Makefile`、`README.md`、`config.mk.example`、`python/filelist.py`、
`python/risk_rules.py`、`skills/.../risk-model.md`、
`skills/.../stages-and-commands.md`、`tests/test_filelist.py`、
`tests/test_skill.py`

### 行為變化

**1. 路徑對帳從死碼變成真的會執行**

`reconcile()` 與 `FILELIST.PROJECT_MISMATCH` 規則早就寫好也測過了，但
`Makefile` 的 `check-files` 沒有傳 `--fileset-list`，所以在真實流程裡從未生效。

現在 `check-files` 直接把 `.xpr` 當 XML 讀（會展開 `$PPRDIR` / `$PSRCDIR`，
跳過 `sim_1` 這類 simulation fileset），所以**仍然不需要 Vivado**；
讀不到才退而使用階段 2 產生的 `manifests/filelist_<run>_sources.txt`；
兩者都沒有就報 `FILELIST.PROJECT_NOT_CHECKED`，**不會靜靜跳過**。

**2. 模組層級檢查**（真正擋住 elaboration 那一段）

掃出所有 `module` / `entity` 宣告與實例化，找出被用到卻沒有定義的模組。
這是路徑對帳擋不住的一類問題 —— 兩份清單可以完全一致，卻仍有模組沒人定義。

依信心分成兩級：

| 情況 | 規則 | 嚴重度 |
|---|---|---|
| 模組定義**就在磁碟上某個檔案**，只是沒被加進來 | `FILELIST.MODULE_FILE_MISSING` | BLOCKER |
| 模組在掃描範圍內哪裡都找不到 | `FILELIST.MODULE_UNDEFINED` | CRITICAL |
| 專案設定的 top module 找不到 | `FILELIST.TOP_NOT_FOUND` | BLOCKER |

**`blk_mem_gen_1216x80` 這個 case 從這一版開始就會被抓到**，
以 `FILELIST.MODULE_UNDEFINED`（CRITICAL）出現在階段 1。

誤判防護：內建 Xilinx UNISIM primitive 清單、專案 `.xci` 的 IP 名稱自動視為
已定義、`config.mk` 的 `IGNORE_MODULES` 可再排除。

### 新增設定

```make
IGNORE_MODULES = my_encrypted_ip vendor_macro
```

---

## 無 git 的更新步驟

1. 備份工作站上的 `config.mk`（那是你的專案設定，**不要覆蓋**）
2. 把新版整包複製過去，或依上面的檔案清單逐檔複製
   —— 注意 `scripts/` 是**新目錄**
3. `chmod +x scripts/*.sh`
4. `sh scripts/checksums.sh` 與下面的〈檔案指紋〉比對
5. `make test` 確認全綠（不需要 Vivado）
6. 依上面「要你動手的兩件事」改 `config.mk` 與你的 IP 產生腳本
7. `make check-files` 看 `blk_mem_gen_1216x80` 是否出現

---

## 檔案指紋（dcb9b92 之後）

`sh scripts/checksums.sh` 的輸出。`scripts/checksums.sh` 自己那行會因為
本檔與它自身的內容而變動，其餘應該完全一致。

```
a96e0798ddbb  Makefile
e1cc31b3761c  README.md
6a47e92014aa  config.mk.example
8530e2ab19e2  docs/opencode-integration.md
5bd6fced8397  docs/skill-authoring.md
8297fa6ba369  examples/sample_project.xpr
a0bfd83be88f  python/analyze_run.py
b682f6d4c5c2  python/banner.py
f19693c5c8d0  python/check_reports.py
f26632250290  python/environment.py
ab3c799c4a1c  python/filelist.py
0088a0d9740a  python/flow_summary.py
0c8f3f0942b6  python/manifest.py
141a0cef7e43  python/precheck.py
d10cc4d24402  python/report_tables.py
40a418f3079d  python/risk_report.py
def343c16fdf  python/risk_rules.py
3f7837a78c53  python/signoff_report.py
d45c63fab3a3  python/trend.py
b404bca96f8e  python/vivado_log.py
a1b74106bf73  python/vivado_report_parser.py
d7b946e81eea  python/vivado_reports.py
8057698b1d92  python/waivers.py
cde9286c0fca  scripts/precheck.sh
b08f85bac614  scripts/with_banner.sh
bb91fbaccd0f  skills/vivado-fpga-flow/SKILL.md
e87fe69079b2  skills/vivado-fpga-flow/references/fpga-interpretation.md
4ecb32a423f9  skills/vivado-fpga-flow/references/risk-model.md
ba0bda5c7ef8  skills/vivado-fpga-flow/references/stages-and-commands.md
162986ef6b87  tcl/check_environment.tcl
c84c68b079d2  tcl/elaborate_check.tcl
485732cfafaa  tcl/generate_missing_ip.tcl
73e167dca5da  tcl/preflight_and_run.tcl
8f2f42389ec7  tcl/timing_report_hooks.tcl
eda0a5bb8f76  tests/run_tests.sh
e5af73ec12f4  tests/test_banner.py
93c173e25e26  tests/test_environment.py
911434e4ea5d  tests/test_filelist.py
88da1801c33e  tests/test_gen_ip.tcl
b711f38e5a93  tests/test_manifest.py
567c559d1892  tests/test_parser.py
fa1d8aaf7d62  tests/test_precheck.py
0466a70798ed  tests/test_preflight.tcl
18e8e7617017  tests/test_reports.py
f3a25e65850f  tests/test_risk_rules.py
ff6e0a0e9e50  tests/test_signoff.py
2bd13157aaa4  tests/test_skill.py
78ef11d502fd  tests/test_vivado_log.py
5e3d28a618db  tests/test_waivers.py
3ba937b32cd0  tests/vivado_stub.tcl
```

`examples/` 底下的 `.rpt` 樣本自 `aca652d` 起未變動，為節省篇幅未列出。
