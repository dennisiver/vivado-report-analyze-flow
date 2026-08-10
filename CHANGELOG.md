# Change log

給沒有 git 的離線工作站對照用。最新的在最上面。

---

## 讓 `EXCLUDE` 在階段 1 真的生效

**症狀**：`make check-files` 報出一堆 missing IP，但 Vivado GUI 顯示一切正常。

**先講清楚：那不是誤報。** 典型成因是 file list 還留著已經廢棄的目錄
（例如 `old_mem/`），那些檔案實例化了專案裡已經不存在的 IP。
GUI 乾淨是因為**專案根本沒有那個目錄**，Vivado 從來沒看過那些檔案。
所以是 file list 與專案不同步，階段 1 正確地報出來了。

比對的東西完全在 Vivado 之外：被實例化的模組（regex 掃 file list ∪ `.xpr`
的來源檔）對上有定義的模組，再扣掉 UNISIM primitive、`IGNORE_MODULES`、
以及**`.xpr` 裡以 `.xci` 結尾的檔案路徑**。認得一個 IP 的唯一途徑是它的
`.xci` 字面出現在 `.xpr` 的 `<FileSets>` 裡。

**真正的缺陷**：`EXCLUDE` 這個設定早就存在，也早就接到階段 2
（`preflight_and_run.tcl` 的 `-exclude`），但**階段 1 完全沒有排除機制** ——
`filelist.py` 連 `exclude` 這個字都沒有，`FILELIST_ARGS` 也沒傳。
設了 `EXCLUDE` 的人會發現階段 2 有效、階段 1 靜靜地無效。

### 改了什麼

- `python/filelist.py`：新增 `--exclude <glob>`（可重複）與 `check(exclude=)`。
  被排除的檔案**同時**退出存在性檢查、模組掃描與專案對帳（含專案那一側，
  否則路徑會從清單消失又以 `only_in_project` 冒出來）
- `Makefile`：`FILELIST_ARGS` 接上同一個 `EXCLUDE` 變數，不新增第二個設定
- `python/signoff_report.py`：file list 那段帶出排除數量與樣式
- 文件：`README.md`、`stages-and-commands.md`、`config.mk.example`

### 比對語意

樣式對**絕對路徑**比對，`*` 會跨過 `/`，大小寫敏感 —— 與階段 2 的
`::vra::is_excluded`（Tcl `string match`）完全一致。
**所以寫 `*/old_mem/*`，不是 `old_mem/*`。**
刻意不支援相對寫法：那會讓同一個樣式在階段 1 命中、階段 2 沒命中，
正好是這次要修掉的那種不一致。

### 排除永遠看得見

```
excluded: 依 1 個樣式排除了 2 個檔案（未經檢查）
          */old_mem/*  -> 2 個
```

沒命中任何檔案的樣式會標成 `← 沒有命中任何檔案`，
不會讓打錯字的樣式看起來像生效了。簽核報告也會帶出排除數量。

### 取捨（文件裡也這樣寫）

`EXCLUDE` 是給「**不能改 file list**」的情況用的，例如那份清單同時被模擬流程
或其他工具共用。若 `old_mem/` 真的已經不需要，**正解是從 file list 拿掉** ——
排除只是把一個真實的不同步訊號蓋住。

### 你要做的

`config.mk` 加一行：

```make
EXCLUDE = */old_mem/*
```

---

## 修正：`test_skill.py` 在 Python 3.6/3.7 抽不到 rule

**症狀**：`make test` 有 2 個失敗，都在 `test_skill.py`：

```
test_the_inventory_was_actually_extracted     預期 > 30 條 rule，實際只有 6 條
test_every_rule_the_skill_mentions_exists_in_the_code
                                              skill 提到 46 個 rule ID，程式只抽到 6 個
```

**這不是複製不完整，是 `tests/test_skill.py` 自己的 bug。**

`collect_rules()` 用 AST 從 `risk_rules.py` 抽出 `finding(...)` 的 rule ID，
判斷節點型別時只認 `ast.Constant`。但 `ast.parse` 從 **Python 3.8 起**才產生
`Constant`；**3.6/3.7 的字串字面值是 `ast.Str`、`True` 是 `ast.NameConstant`**。
在舊直譯器上那個分支一條都比不中，只剩「LOG.* 從執行期表格展開」那條路徑還會動
—— `_LOG_CLASS_RULES` 剛好 6 筆，就是你看到的 6。

這個 repo 宣稱支援 Python 3.4+，測試卻沒有做到；離線工作站正是舊直譯器所在之處。

**修正**：`tests/test_skill.py` 新增 `string_literal()` / `is_true_literal()`，
同時認得新舊兩種節點形狀（`ast.Str` 在 3.12 已移除，故用 `getattr` 取用）。
以 3.6/3.7 的節點形狀驗證過：抽出 57 條 rule、21 條阻擋上板，與新直譯器完全一致。
另加 5 個測試釘住這件事，並讓數量不足時的錯誤訊息直接指出可能是直譯器版本問題。

產品程式碼完全沒有這個問題 —— 只有這個測試輔助函式用到 `ast`。
順帶用 `feature_version=(3, 6)` 檢查過全部 29 個 Python 檔，語法皆相容。

### 順便從那個 46 看出來的事

錯誤訊息裡的 **46 不是「應該有幾條 rule」，是不吻合的數量**。
文件提到的 ID 數減掉抽到的 6 條就是它 —— 所以你的 skill 文件提到 52 個 ID。

各版本 `references/risk-model.md` 提到的 ID 數：

| 版本 | 提到的 rule ID 數 |
|---|---|
| `aca652d` 之前 | **52** |
| `aca652d` 之後（含現在） | 56 |

**52 表示你的 `skills/` 還是 `aca652d` 之前的版本**，但 `tests/` 已經是新的
—— 也就是那份 copy 是混合的。只補 `tests/test_skill.py` 會修掉抽取問題，
但接著會**正當地**報出 `aca652d` 新增的 4 條規則沒有被文件涵蓋。

**請整包複製，不要挑檔案**，然後用 `sh scripts/checksums.sh` 對指紋確認。

修好之後這些數字才是對的：程式碼 **57 條 rule、其中 21 條阻擋上板**，
文件提到 56 條（唯一沒提到的 `QOR.LOW_SCORE` 不阻擋上板，文件不強制涵蓋）。
自己確認的指令：

```bash
python3 -c "
import sys; sys.path.insert(0,'tests')
import test_skill as T
r = T.collect_rules(); m = set(T._RULE_ID.findall(T.skill_text()))
print('程式碼', len(r), '條，阻擋上板', sum(1 for v in r.values() if v))
print('文件提到', len(m), '條；提到但程式沒有：', sorted(m - set(r)))
"
```

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
4. 在新舊兩份各跑一次 `sh scripts/checksums.sh` 再 `diff`（見〈怎麼確認版本〉）
5. `make test` 確認全綠（不需要 Vivado）
6. 依各節「你要做的」改 `config.mk` 與你的 IP 產生腳本
7. `make check-files` 確認結果符合預期

---

## 怎麼確認版本

**不要靠背指紋表** —— 這份檔案自己的雜湊會隨每次更新而變，寫在裡面永遠是錯的。
兩份 copy 各跑一次再 diff 才是可靠的做法：

```bash
sh scripts/checksums.sh > /tmp/old.txt    # 工作站上那份
sh scripts/checksums.sh > /tmp/new.txt    # 新複製過去的
diff /tmp/old.txt /tmp/new.txt
```

`checksums.sh` 會排除 `config.mk` 與 `timing_analysis/`，
所以你的專案設定與輸出不會混進來。

只有一份、想知道停在哪一版時，看這幾個標記：

| 檢查 | 代表 |
|---|---|
| `ls scripts/` 不存在 | 早於 `dcb9b92`（沒有 banner、pre-check、gen-ip） |
| `ls CHANGELOG.md` 不存在 | 早於 `57b7e7d` |
| `ls examples/sample_project.xpr` 不存在 | 早於 `aca652d`（沒有模組層級檢查） |
| `grep -c exclude python/filelist.py` 是 0 | 早於本次的 `EXCLUDE` 修正 |
| `grep -o '全部 [0-9]* 條' skills/vivado-fpga-flow/SKILL.md` 印出 47 | 早於 `7154ef0` |
