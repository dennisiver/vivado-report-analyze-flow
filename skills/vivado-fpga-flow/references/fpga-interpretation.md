# FPGA 領域判讀參考

風險分級告訴你「有多嚴重」，這份告訴你「為什麼會這樣、怎麼修」。
給建議時用這裡的內容，不要泛泛地說「請優化時序」。

## 目錄

- [時序路徑怎麼看](#時序路徑怎麼看)
- [Setup 與 hold 的根本差異](#setup-與-hold-的根本差異)
- [跨時脈域（CDC）](#跨時脈域cdc)
- [約束沒生效](#約束沒生效)
- [I/O 約束](#io-約束)
- [資源使用率與壅塞](#資源使用率與壅塞)
- [IP 與可重現性](#ip-與可重現性)

## 時序路徑怎麼看

摘要的 Top 10 路徑每條都有 slack、path group、起訖點、logic levels、
以及 logic/route 延遲佔比。判讀重點：

**route 佔比過高（> 70%）** —— 延遲主要花在繞線而不是邏輯。這通常代表該區域壅塞，
訊號被迫繞遠路。特徵是同樣的 RTL 重跑一次結果會明顯不同。

建議方向：降低該區域的資源使用率、檢視 floorplan（考慮 `pblock` 把相關邏輯圈在一起）、
啟用 physical optimization（`phys_opt_design`）。
**單純加 pipeline 對 route-dominated 的路徑效果有限**，因為問題不在邏輯層數。

**logic levels 過多（> 15）** —— 組合邏輯層數太深。這是 RTL 結構的問題，
佈局繞線改善不了。

建議方向：在該路徑插 pipeline stage、把寬的算術運算拆段、
檢查是否有不必要的優先權編碼（長 if-else 鏈會合成成串接的 mux）。

**同一個模組反覆出現在 Top 10** —— 那個模組是瓶頸，優先處理它，
比逐條路徑修有效得多。

**跨 clock domain 的路徑出現在 timing 報告裡** —— 先確認它是不是本來就不該被
同步分析。若確實非同步，該加的是 `set_clock_groups -asynchronous` 或
`set_max_delay -datapath_only`。但要提醒使用者：
**加 exception 只是讓 timing 報告不再分析它，並沒有解決 metastability** ——
同步器還是要加。

## Setup 與 hold 的根本差異

這是整個風險模型最重要的區分，回答時要講清楚：

**Setup 違規**：資料到得太晚。與時脈週期成正比 —— 降低頻率就會改善。
所以可以降頻先上板做其他項目的 bring-up，只是原設計頻率下的行為未經驗證。

**Hold 違規**：資料變得太早。**與時脈頻率無關** —— 降頻完全沒有幫助，
因為 hold 檢查的是「資料在時脈邊緣後是否維持夠久」，這跟週期長度無關。

hold 違規在實機上的表現是隨溫度、電壓、晶片批次變動的隨機錯誤，
而且通常無法穩定重現 —— 這使它成為最不該帶上板的一種問題。

常見成因：跨時脈域路徑缺少同步器、clock skew 過大、I/O 的 hold constraint 有誤。
修正後**必須重跑 implementation**。

## 跨時脈域（CDC）

`CDC.CRITICAL`（CDC-1 等）代表有訊號跨越時脈域但沒有同步結構。
這是「實驗室測起來正常、上板後偶發錯誤、且隨溫度變動」最典型的根因 ——
用一般的功能驗證手段幾乎無法重現，所以它被評為 BLOCKER。

修法依訊號性質不同：

- **單 bit 控制訊號** → 兩級（含）以上同步器，並在同步器的 register 上標註
  `ASYNC_REG = TRUE`。這個屬性告訴 Vivado 把兩顆 FF 放在同一個 slice，
  讓它們之間的繞線延遲最小，最大化 MTBF。
- **多 bit 資料** → **不要**每個 bit 各接一個同步器。各 bit 到達時間不一致時，
  接收端會取到從未實際存在過的組合值。改用：
  - gray code（適合計數器類，一次只變一個 bit）
  - handshake（req/ack，適合低速控制）
  - 非同步 FIFO（適合資料流）
- **`CLK.NO_COMMON_CLOCK`** → 兩個 clock 沒有共同的 primary clock，
  它們之間的相位關係在實機上是不確定的。Vivado 仍會算出 slack，但那個數字不成立。
  確認是否為非同步關係，是的話明確宣告 `set_clock_groups -asynchronous` 並加同步器。

## 約束沒生效

這是這個 flow 最常抓到、也最容易被忽略的一類問題。三個層次：

1. **XDC 檔沒加進 fileset** → 階段 2 會抓到。Vivado 自己的 out-of-date 偵測
   **永遠不會觸發**，因為那個檔案從頭到尾就不屬於這個專案。
2. **XDC 有讀進來，但某一行指向不存在的物件** → `LOG.CONSTRAINT_NOT_APPLIED`。
   Vivado 只印一行 `No objects matched` 的 WARNING 就繼續跑。
   常見成因：port/cell 名稱打錯，或 RTL 改名後 XDC 沒跟著改。
3. **約束存在但沒涵蓋所有路徑** → `check_timing` 的
   `unconstrained_internal_endpoints` 或 `no_clock` 不為 0。

三者的共同後果是一樣的：**timing 報告上那些漂亮的數字，是在一組比你以為的更少的
約束下算出來的**。所以看到這類問題時，要先解決約束，
而不是根據 WNS 去改 RTL —— 那個 WNS 本身就不可信。

## I/O 約束

`TIMING.NO_IO_DELAY` 代表有 port 缺少 `set_input_delay` / `set_output_delay`，
板級介面的時序完全沒有被驗證。內部 timing 再乾淨，也不保證這些訊號在實際 PCB 上
與對端元件之間能正確收送。

預設評為 CRITICAL（阻擋 sign-off 但不阻擋 bring-up），
但**如果那些 port 正是使用者這次要驗證的介面，實質上就是 BLOCKER** ——
要主動提醒這一點。

`DRC.BITSTREAM_BLOCKING`（`NSTD-1` / `UCIO-1`）更嚴重：有 I/O 沒指定
`IOSTANDARD` 或 `PACKAGE_PIN`。Vivado 預設會直接擋下 `write_bitstream`，
理由是未指定電氣標準的接腳上板後可能造成介面完全不動作，甚至有電性損傷風險。
**不要建議使用 `-force` 略過。**

## 資源使用率與壅塞

`UTIL.CRITICAL`（≥ 90%）的問題不只是「快不夠用了」，而是**結果變得不可重複**：
繞線資源緊張時，改一行 RTL 就可能讓 WNS 大幅變動。
這會讓後續除錯失去基準 —— 你無法判斷某個改動到底有沒有幫助。

建議方向：共用重複邏輯、用 BRAM 取代分散式記憶體（LUTRAM）、
檢查是否有過度的迴圈展開或陣列複製、或改用更大的元件。

`CTRLSET.HIGH` —— control set（clock/reset/enable 的組合）過多會讓 register
難以緊密封裝進同一個 slice，推高使用率並惡化時序。
常見成因是對每個 register 都給獨立的 enable 訊號，或到處都用非同步 reset。

## IP 與可重現性

`IP.MISSING_PRODUCTS` —— IP 沒有可用的實作產物，合成時會被當黑盒子或直接失敗。
若僥倖跑完，電路中缺的就是那個 IP 的功能。修法：`generate_target all` 或 `synth_ip`。

`IP.NEEDS_UPGRADE` —— IP 是用較舊版本的 Vivado 產生的。它通常還能用，
但你合成進去的是舊版本的實作，也拿不到後續版本修掉的問題。

`PROV.DIRTY_INPUTS` —— 有未 commit 的設計檔。這件事在硬體驗證的脈絡下特別重要：
**如果之後在板上發現問題，你將無法確定當時燒錄的到底是哪一版，也無法重建相同的
bitstream。** 對 sign-off 而言這不可接受。

`ENV.VIVADO_CHANGED` —— 換了工具版本，先前累積的結果不再可比。
歷史趨勢要以版本切換點為界分開看，IP 也可能需要重新產生。
