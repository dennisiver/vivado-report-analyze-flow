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

## 兩層結構

指引分成兩層，因為它們的成本不同：

| 層 | 放哪裡 | 何時載入 | 內容 |
|---|---|---|---|
| 1. 護欄 | `AGENTS.md` | **每一輪都載入** | 只放不能延遲的規則 |
| 2. 完整指引 | `skills/vivado-fpga-flow/` | 需要時才載入 | 階段、判定模型、規則、FPGA 建議 |

第一層必須極短，因為它的成本是每一輪都要付的，即使使用者問的是完全無關的事。
但它又不能省 —— 「不要讀 `raw/*.rpt`」如果等到需要時才知道，
context 可能已經被上萬行報告塞爆了。

## 第一層：貼進 `AGENTS.md` 的護欄

把下面這段複製進 FPGA 專案根目錄的 `AGENTS.md`，
並把 `<FLOW>` 換成實際安裝路徑：

```markdown
## Vivado 合成分析

本專案的 Vivado 建置透過 `make -f <FLOW>/Makefile` 執行，
結果已整理在 `timing_analysis/` 底下。

- timing / CDC / DRC /「能不能上板」相關問題，**先讀
  `timing_analysis/latest_flow.md`**（跨階段總覽，約 30 行）。
  需要單一階段細節再讀 `timing_analysis/latest_<stage>.md`。
- **不要讀 `timing_analysis/raw/` 底下的 `.rpt` 與 `.log`** ——
  那是原始輸出，動輒上萬行，讀進來會塞爆 context，
  而摘要裡已經有全部的彙總資訊。
- **不要讀 `signoff_*.md`** —— 那是給人審閱簽核用的完整報告，
  內容你在上面兩份檔案裡都已經有了。
- 「未檢查」不等於「沒問題」。摘要會列出未執行的階段與未解析的報告，
  要據實轉達，不要用其他階段的結果推測。
- 需要完整的判讀規則、階段差異、FPGA 修法建議時，
  讀 `<FLOW>/skills/vivado-fpga-flow/SKILL.md`，
  更深的細節在同目錄的 `references/*.md`。
```

這段刻意只有十幾行。其餘全部推到第二層。

## 第二層：完整指引

`skills/vivado-fpga-flow/` 是標準格式的 Agent Skill，內容包含
八階段、兩個判定與四個嚴重度、完整規則表、階段差異、FPGA 領域判讀。

**若 OpenCode 1.17.3 支援 skill** —— 比照 Claude Code 安裝
（見 `docs/skill-authoring.md`），agent 會在相關時自動載入。

**若不支援** —— 上面的護欄已經指明路徑，agent 需要時可以自己去讀。
效果接近，只是由 agent 自行決定何時讀，而不是由框架自動觸發。

無論哪種情況，**內容來源都是同一份**，
所以 Claude Code 那邊與 OpenCode 這邊不會分歧 ——
這是把細節集中在 skill 而不是複製一份到 `AGENTS.md` 的主要理由。

OpenCode 是否支援 skill 需要實際確認該版本的文件。

## 給使用者的提醒

- 摘要檔會**每次覆寫**。需要保留某次結果時，`timing_analysis/history/` 底下
  有依時間戳記命名的完整 JSON，`signoff_<時間>.md` 也不會被覆寫。
- 使用者若還沒跑過某個階段，agent 應該建議對應的 `make` target，
  而不是從其他階段的結果推測。
- 改了 RTL 或 XDC 後想快速確認，`make check-files` 不需要 Vivado，幾秒鐘就有結果。
