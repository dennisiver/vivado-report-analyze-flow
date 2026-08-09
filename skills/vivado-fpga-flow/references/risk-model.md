# 風險模型完整參考

本檔對應 `python/risk_rules.py`。改了那支程式就要同步這裡 ——
`tests/test_skill.py` 會做 rule ID 雙向對帳，漏改會讓測試失敗。

## 目錄

- [嚴重度怎麼推導](#嚴重度怎麼推導)
- [階段差異](#階段差異)
- [全部規則](#全部規則)
- [Log 訊息分類](#log-訊息分類)
- [閾值](#閾值)
- [Waiver](#waiver)
- [未檢查的項目](#未檢查的項目)

## 嚴重度怎麼推導

每條規則帶兩個布林值，嚴重度由它們推導，所以分級永遠自洽：

```
blocks_bringup=True                     → BLOCKER
blocks_bringup=False, signoff=True      → CRITICAL
兩者皆 False，notable=True              → WARNING
兩者皆 False，notable=False             → INFO
```

被豁免（waived）的項目會把兩個旗標清成 False，但**保留原本的嚴重度**，
因為被接受的風險仍然是那個等級的風險，只是不再擋流程。

## 階段差異

`evaluate(stage_kind=...)` 接受 `"synth"` 或 `"impl"`。
合成後的時序是尚未佈局的估算值，用繞線後的標準去評會每次都噴假警報：

| 項目 | `synth` | `impl` |
|---|---|---|
| Setup 違規（一般幅度） | `TIMING.SETUP_VIOLATION_SYNTH`，WARNING | `TIMING.SETUP_VIOLATION`，CRITICAL |
| Setup 缺口過大 | `TIMING.SETUP_SEVERE_SYNTH`（> 週期 50%），CRITICAL | `TIMING.SETUP_SEVERE`（> 週期 10%），BLOCKER |
| Hold 違規 | **完全跳過**（還沒繞線，數字沒有意義） | `TIMING.HOLD_VIOLATION`，BLOCKER |
| Utilization | 只在 > 100% 時報（估算值） | 90% / 80% 兩段 |
| 約束類（`no_clock`、未約束、constraint 沒套用） | **與 impl 同級** | 同左 |
| CDC / clock interaction | **與 impl 同級** | 同左 |

約束類問題兩階段同級，正是早期攔截的價值：那些在合成後就已經確定了。

## 全部規則

### 阻擋上板（BLOCKER，17 條）

| Rule ID | 意義 |
|---|---|
| `TIMING.HOLD_VIOLATION` | hold 違規；與頻率無關，降頻無法迴避 |
| `TIMING.PULSE_WIDTH` | 時脈脈寬低於 primitive 規格，行為未定義 |
| `TIMING.NO_CLOCK` | 有 register 沒有 clock 定義，完全沒被 time 到 |
| `TIMING.COMB_LOOP` | 組合迴路，靜態時序分析無法描述 |
| `TIMING.SETUP_SEVERE` | setup 缺口 > 週期 10%，降頻幅度已不具代表性 |
| `TIMING.UNCONSTRAINED_SEVERE` | 未約束 endpoint > 總數 1%，WNS 不能代表設計 |
| `CDC.CRITICAL` | 未同步的跨時脈域（CDC-1 等），metastability |
| `CLK.NO_COMMON_CLOCK` | 時脈間無共同來源卻被同步分析，slack 不成立 |
| `DRC.BITSTREAM_BLOCKING` | `NSTD-1` / `UCIO-1`，會擋 write_bitstream，I/O 無電氣標準 |
| `DRC.ERROR` | DRC Error，違反硬體基本規則 |
| `METH.TIMING_UNSAFE` | `TIMING-6/7/9`，Xilinx 標記為時序結果不可信 |
| `IP.MISSING_PRODUCTS` | IP 的 output products 缺失，等同黑盒子 |
| `FILELIST.MISSING_FILE` | file list 引用了不存在的檔案 |
| `FILELIST.MISSING_INCDIR` | `+incdir+` 目錄不存在 |
| `FILELIST.PROJECT_MISMATCH` | file list 與 `.xpr` 不一致 |
| `LOG.CONSTRAINT_NOT_APPLIED` | constraint 指向不存在的物件，那一行沒生效 |
| `LOG.UNBOUND_MODULE` | 模組沒有定義，被當黑盒子 |
| `LOG.ERROR` | Vivado 自己判定為 ERROR |
| `BITSTREAM.MISSING` | 要求產生 bitstream 但檔案不存在 |

### 阻擋 sign-off（CRITICAL）

| Rule ID | 意義 |
|---|---|
| `TIMING.SETUP_VIOLATION` | setup 違規；可降頻 bring-up，但原頻率未驗證 |
| `TIMING.SETUP_SEVERE_SYNTH` | 合成後缺口 > 週期 50%，implementation 救不回來 |
| `TIMING.UNCONSTRAINED_ENDPOINTS` | 有 endpoint 未被時序約束 |
| `TIMING.NO_IO_DELAY` | port 缺 input/output delay，板級介面未驗證 |
| `CDC.WARNING` | 多 bit 跨域等疑慮（CDC-4 等） |
| `CLK.PARTIAL_FALSE_PATH` | exception 只覆蓋部分路徑，可能掩蓋問題 |
| `DRC.CRITICAL_WARNING` | 其他 Critical Warning 等級 DRC |
| `METH.CLOCK_PATH` | `TIMING-14/15`，clock path 上有 LUT/latch |
| `METH.CRITICAL` | 其他 Critical 等級方法學項目 |
| `UTIL.CRITICAL` | 資源使用率 ≥ 90%，結果不可重複 |
| `IP.NEEDS_UPGRADE` | IP 需要 upgrade |
| `IP.LOCKED` | IP 被 lock，可能與目前參數不一致 |
| `ENV.VIVADO_CHANGED` | Vivado 版本改變，先前結果不再可比 |
| `PROV.DIRTY_INPUTS` | 有未 commit 的設計檔，bitstream 無法由 git 重現 |
| `FILELIST.DUPLICATE_MODULE` | 同名模組定義在多個檔案，取用哪份看讀取順序 |
| `FILELIST.PARSE_ERROR` | file list 無法完整解析 |
| `LOG.INFERRED_LATCH` | 推論出非預期的 latch |
| `LOG.UNDRIVEN_NET` | 訊號未驅動或多重驅動 |
| `META.REPORT_UNAVAILABLE` | 某項分析未執行或解析失敗（安全相關的報告） |

### WARNING

| Rule ID | 意義 |
|---|---|
| `TIMING.SETUP_VIOLATION_SYNTH` | 合成後未收斂，估算值，implementation 可能修掉 |
| `TIMING.ROUTE_DOMINATED` | 最差路徑 route 佔比 > 70%，壅塞徵兆 |
| `TIMING.DEEP_LOGIC` | 最差路徑 logic levels > 15 |
| `UTIL.HIGH` | 資源使用率 ≥ 80% |
| `CTRLSET.HIGH` | control set 數量相對 register 偏高 |
| `DRC.WARNING` / `METH.WARNING` | 一般等級 |
| `LOG.WIDTH_MISMATCH` | 位寬不匹配或被截斷 |
| `LOG.PLACEMENT_QUALITY` | 佈局／繞線品質不佳 |
| `ENV.OS_CHANGED` | OS 改變 |
| `ENV.OS_UNSUPPORTED` | OS 不在 Vivado 2024.2 支援清單 |
| `PROV.PREFLIGHT_WARNINGS` | pre-flight 稽核有警告 |

### INFO

`DRC.ADVISORY`、`METH.ADVISORY`，以及非安全相關報告的 `META.REPORT_UNAVAILABLE`。

## Log 訊息分類

有些最要命的問題只出現在 log，任何 report 都看不到。
**不能只看 severity** —— 下面前兩類在 Vivado 裡都只是 `WARNING`，
只抓 `CRITICAL WARNING` 以上會把最有價值的漏掉。
比對方式是 message ID **與**訊息文字雙軌，文字是能跨版本存活的那一半。

| 類別 | 文字特徵 | 分級 |
|---|---|---|
| `CONSTRAINT_NOT_APPLIED` | `No objects matched`、`expects at least one object` | BLOCKER |
| `UNBOUND_MODULE` | `black box`、`unable to bind`、`cannot find module` | BLOCKER |
| `INFERRED_LATCH` | `inferring latch` | CRITICAL |
| `UNDRIVEN_NET` | `does not have driver`、`multiple drivers` | CRITICAL |
| `WIDTH_MISMATCH` | `truncated`、`width mismatch` | WARNING |
| `PLACEMENT_QUALITY` | `poor placement`、`congestion` | WARNING |

最典型的例子：

```
WARNING: [Vivado 12-507] No objects matched 'get_ports pcie_refclk_p'
```

XDC 有加進 fileset、也被讀了，但這一行指向不存在的 port 所以完全沒生效 ——
後面所有 timing 數字都是在一組不完整的約束下算出來的。

## 閾值

集中在 `python/risk_rules.py` 的 `DEFAULT_THRESHOLDS`，可依團隊標準調整：

| 名稱 | 預設 | 用途 |
|---|---|---|
| `setup_severe_fraction` | 0.10 | impl 階段 setup 缺口升級為 BLOCKER 的門檻（佔週期比例） |
| `setup_severe_fraction_synth` | 0.50 | synth 階段的對應門檻，因為那是估算值 |
| `unconstrained_severe_fraction` | 0.01 | 未約束 endpoint 佔比升級門檻 |
| `route_dominated_percent` | 70.0 | route 佔比警戒線 |
| `deep_logic_levels` | 15 | logic levels 警戒線 |
| `util_critical_percent` | 90.0 | 使用率 CRITICAL 門檻 |
| `util_high_percent` | 80.0 | 使用率 WARNING 門檻 |
| `control_sets_per_register` | 0.20 | control set 相對 register 的比例 |
| `qor_score_warn` | 3 | QoR 評分警戒線（滿分 5） |

## Waiver

專案根目錄的 `waivers.json`：

```json
{"waivers": [
  {"id": "DRC.CRITICAL_WARNING",
   "match": {"rule": "RTSTAT-6"},
   "reason": "已確認為 debug 訊號，不影響功能",
   "approved_by": "Dennis",
   "date": "2026-08-09",
   "evidence_digest": "ab12cd34ef56"}
]}
```

`evidence_digest` 從 sign-off 報告的「佐證 digest」欄位複製。

**關鍵：waiver 綁定佐證內容的 hash。** 該項目的實際內容一改變
（違規數量增加、換成別的 instance），digest 不再吻合，
豁免自動失效並重新阻擋，同時列為「已失效的豁免」。
這樣「不重複踩雷」才不會變成「把真問題永久靜音」。

省略 `evidence_digest` 則接受當下的任何內容 —— 比較寬鬆，不建議用在 BLOCKER 上。
格式錯誤的 `waivers.json` **不會豁免任何項目**（失敗時傾向安全側）。

## 未檢查的項目

`META.REPORT_UNAVAILABLE` 是刻意設計的：某份報告沒跑成功或無法解析時，
**絕不呈現為「沒有問題」**，而是明確列為未檢查並阻擋 sign-off
（`control_sets`、`qor_assessment` 這類最佳化提示除外，只列為 INFO）。

風險報告最容易致命的地方就是「看起來很乾淨，其實根本沒檢查」。
回答使用者時要沿用這個區分。
