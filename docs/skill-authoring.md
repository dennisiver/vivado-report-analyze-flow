# 這個 flow 的 Agent Skill：為什麼、怎麼維護、怎麼重新產生

`skills/vivado-fpga-flow/` 是這個 repo 附的 Agent Skill。
這份文件說明它為什麼存在、改了程式碼要同步哪裡，以及要請 AI 重新產生或擴充時該給什麼指引。

## 目錄

- [為什麼做成 skill](#為什麼做成-skill)
- [結構與取捨](#結構與取捨)
- [同步對照表](#同步對照表)
- [測試擋得住什麼、擋不住什麼](#測試擋得住什麼擋不住什麼)
- [安裝](#安裝)
- [請 AI 重新產生或擴充時](#請-ai-重新產生或擴充時)

## 為什麼做成 skill

三個具體理由，不是「因為現在流行」：

**1. 漸進式揭露本來就是這個 repo 的設計原則。**
資料層早就這樣做了：`latest_flow.md` → `latest_<stage>.md` → `risk_<stage>.md` → `raw/`。
Skill 是把同一個原則套用到**指令**上 —— 三層載入（描述 → 本體 → references）
和這個 flow 三層輸出的思路一致。

**2. Context 經濟，對本地小模型特別重要。**
`AGENTS.md` 的內容**每一輪都會載入**，即使使用者問的是完全無關的事。
Qwen3.6-27B 的 context 有限，把 150 行 FPGA 判讀規則常駐在那裡是實打實的浪費。
Skill 的本體只在相關時才載入。

**3. 領域知識才是精華。**
「hold 違規與頻率無關所以阻擋上板，setup 違規可以降頻先測」——
通用模型不會知道這件事。這種判斷正是 skill 該封裝的東西，
而不是每次都靠使用者在對話裡重講一遍。

### 但 skill 不能取代 AGENTS.md 的護欄

有一條規則必須**永遠**生效：不要去讀 `timing_analysis/raw/` 下的原始報告。
如果等到 skill 觸發才知道，context 可能已經被上萬行報告塞爆了 —— 那時才知道就太遲。

所以正確的分工是：

- `AGENTS.md`：**極短的永遠載入護欄**（不要讀什麼、先讀哪一份）
- Skill：其餘全部（階段、判定模型、規則、FPGA 建議）

`docs/opencode-integration.md` 就是照這個兩層結構寫的。

## 結構與取捨

```
skills/vivado-fpga-flow/
  SKILL.md                            本體，觸發後全文載入
  references/
    risk-model.md                     規則表、階段差異、閾值、waiver
    stages-and-commands.md            八階段細節、選項、失敗處理
    fpga-interpretation.md            FPGA 領域判讀與修法建議
```

### 什麼進本體、什麼進 references

判斷準則是「**日常每次都會用到嗎**」：

| 進 SKILL.md 本體 | 進 references |
|---|---|
| 讀哪一份檔案、不讀哪一份 | 完整的 rule ID 表 |
| 八階段與對應指令（一張表） | 每個階段的所有選項與失敗處理 |
| 兩個判定與四個嚴重度 | 每條規則的兩個旗標與理由 |
| 回答問題的順序 | 具體的 FPGA 修法建議 |
| 兩個最容易誤判的地方 | 閾值數字、waiver 格式 |

本體有行數上限（目前 200 行，由測試強制）。理由和 flow 自己對
`latest_<run>.md < 120 行` 的斷言一樣：**這是每次都要付的 context 成本**。

`description` 也有長度上限（1400 字元），因為它是**每一輪**都要付的成本，
不管 skill 有沒有被用到。

### 為什麼不 bundle scripts

Skill 支援 `scripts/`，但這裡刻意不用：實際的程式都在 repo 的 `python/`、`tcl/`，
複製一份進 skill 只會製造兩份會漂移的程式碼。
Skill 的做法是指向 `make` target 與 repo 內的腳本，並在 SKILL.md 開頭
明講「需要 flow 已安裝且專案有 `config.mk`」這個前提。

## 同步對照表

改了 repo 就要同步 skill。**粗體的項目有測試會擋**，其餘要靠人工。

| 改了什麼 | 要同步哪裡 |
|---|---|
| `risk_rules.py` **新增／改名規則** | **`references/risk-model.md` 的規則表**；BLOCKER 級的還要在 skill 任一處被提到 |
| `risk_rules.py` 的 **`DEFAULT_THRESHOLDS`** | **`references/risk-model.md` 的閾值表**（數字會被逐一比對） |
| `risk_rules.py` 的規則敘述、風險說明文字 | `references/risk-model.md` 的意義欄（人工） |
| `vivado_log.py` 的 `HIGH_RISK_MESSAGES` | `references/risk-model.md` 的 log 分類表（人工） |
| 新增 `make` target 或階段 | `SKILL.md` 的階段表、`references/stages-and-commands.md`（人工） |
| `preflight_and_run.tcl` 新增選項 | `references/stages-and-commands.md` 的選項表（人工） |
| **輸出檔名或位置改變** | **`SKILL.md` 的讀取規則**（部分有測試）、`references/stages-and-commands.md`、`docs/opencode-integration.md` |
| 階段差異的評分邏輯（`stage_kind`） | `references/risk-model.md` 的階段差異表；**關鍵幾條有測試比對實際輸出** |

## 測試擋得住什麼、擋不住什麼

`tests/test_skill.py`，跟著 `make test` 一起跑。

**擋得住（自動）：**

- frontmatter 缺 `name` / `description`、`name` 格式不對或與目錄不符
- 本體或描述超過長度上限
- reference 檔沒被本體引用（等於永遠不會被載入的死檔）
- 本體指向不存在的 reference 檔
- **skill 提到了 `risk_rules.py` 裡不存在的 rule ID**（規則被改名或刪除）
- **`risk_rules.py` 新增了會阻擋上板的規則，但 skill 完全沒提**
- 文件裡的閾值數字與 `DEFAULT_THRESHOLDS` 不符
- 有閾值沒被文件化
- 幾條關鍵事實與實際 `evaluate()` 輸出不符：
  hold 在 impl 是 BLOCKER 且在 synth 不存在、setup 在兩階段的等級、
  `LOG.CONSTRAINT_NOT_APPLIED` 是 BLOCKER

rule ID 清單是用 AST 從 `risk_rules.py` 的 `finding(...)` 呼叫抽出來的，
不是手動維護的清單，所以不會自己落後。`LOG.*` 那幾條是執行時組出來的，
從 `_LOG_CLASS_RULES` 展開。

測試裡有一條 `test_the_reconciliation_would_actually_catch_a_gap`，
刻意模擬「多了一條沒寫進 skill 的 BLOCKER」並確認檢查會失敗 ——
避免寫出一個永遠會通過的假測試。

**擋不住（要人工看）：**

- **敘述文字的正確性。** 測試只確認 rule ID 有被提到，不會判斷旁邊那句解釋對不對。
  規則的行為改了但 ID 沒變時，文字可能已經在說謊。
- WARNING / INFO 等級規則的遺漏（只有 BLOCKER 級強制要求被提到）。
- FPGA 建議的品質 —— `fpga-interpretation.md` 的內容是領域判斷，測不出來。
- skill 實際會不會被觸發。那要靠真的裝起來問問題（見下）。

## 安裝

### Claude Code

複製或 symlink 到 skills 目錄的其中一處：

```bash
# 只給某個專案用
mkdir -p .claude/skills
ln -s <flow>/skills/vivado-fpga-flow .claude/skills/vivado-fpga-flow

# 全域
mkdir -p ~/.claude/skills
ln -s <flow>/skills/vivado-fpga-flow ~/.claude/skills/vivado-fpga-flow
```

用 symlink 的好處是 repo 更新時 skill 自動跟著更新，不會有兩份。

裝好後在一個有 `timing_analysis/` 的專案裡問「這版能不能上板？」，
確認 skill 有被觸發、而且**沒有**去讀 `raw/` 下的檔案。

### OpenCode + Qwen

OpenCode 1.17.3 是否支援 Anthropic 格式的 skill **需要實際確認**
（查該版本文件中是否有 skill 或 `.opencode/skills` 之類的機制）。

- **支援** → 比照上面安裝，並把 `docs/opencode-integration.md` 的第一層護欄
  貼進 `AGENTS.md`。
- **不支援** → 只把第一層護欄貼進 `AGENTS.md`，
  並在裡面指明「需要細節時去讀 `skills/vivado-fpga-flow/references/*.md`」。
  這樣 reference 檔仍然發揮「需要才讀」的作用，只是由 agent 自己決定何時讀，
  內容來源與 Claude Code 那邊完全相同，不會分歧。

## 請 AI 重新產生或擴充時

把以下這段連同這份文件一起給 AI：

> 請依 `docs/skill-authoring.md` 更新 `skills/vivado-fpga-flow/`。
>
> 先讀這些檔案，依序：
> 1. `python/risk_rules.py` —— 規則、兩個旗標、`DEFAULT_THRESHOLDS`、`stage_kind` 差異
> 2. `python/vivado_log.py` —— `HIGH_RISK_MESSAGES` 分類
> 3. `Makefile` —— 各階段的 target
> 4. `README.md` 的「完整分析流程」與「風險分級模型」兩節
> 5. 現有的 `skills/vivado-fpga-flow/SKILL.md` 與三個 reference
>
> 必須遵守的約束：
> - SKILL.md 本體 < 200 行，`description` < 1400 字元（有測試強制）
> - 所有 rule ID 必須真的存在於 `risk_rules.py`，不要自己發明
> - 每一條會 `blocks_bringup=True` 的規則都必須在 skill 裡被提到
> - 閾值數字要與 `DEFAULT_THRESHOLDS` 一致
> - 不要把 README 整段複製進來 —— skill 是給 agent 執行用的，
>   不是給人讀的說明書；只放會影響 agent 行為的內容
> - 不要 bundle `scripts/`，指向 repo 既有的 `make` target 與 Python 腳本
> - 用祈使句，並解釋**為什麼**，不要堆 MUST／ALWAYS
>
> 改完跑 `python3 -m unittest tests.test_skill` 確認全過。
> 若新增了規則，先更新 `references/risk-model.md` 再跑測試。
