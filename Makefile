# =============================================================================
# vivado-report-analyze-flow
#
# 每個階段一個 target，可以單獨執行，也可以用 `make all` 串起來。
#
#   1. cp config.mk.example config.mk
#   2. 編輯 config.mk 填入你的專案路徑
#   3. make help
#
# 各 target 之間刻意「不設 Make 相依」：合成流程各階段的耗時差異太大
# （靜態檢查是秒級，implementation 是數十分鐘），自動連鎖觸發只會帶來意外。
# 要照順序跑請用 `make all`。
# =============================================================================

FLOW_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

-include config.mk

# --- 預設值（可在 config.mk 覆寫）-------------------------------------------
PROJECT   ?=
RTL_DIRS  ?=
XDC_DIRS  ?=
FILELIST  ?=
OUTDIR    ?= timing_analysis
REPO_ROOT ?= .
RUN       ?= impl_1
JOBS      ?= 4
WAIVERS   ?= waivers.json
EXCLUDE   ?=
IGNORE_MODULES ?=
VIVADO    ?= vivado
PYTHON    ?= python3

# 使用者既有的 IP 產生腳本（例如 gen_sram_checkdouble2.tcl）。
# 本工具不自己產生 IP，只負責偵測缺哪些、把清單餵給這支腳本、事後查核。
SRAM_GEN_TCL ?=
GEN_IP_AUTO  ?= 0

PY      := $(FLOW_DIR)/python
TCL     := $(FLOW_DIR)/tcl
SCRIPTS := $(FLOW_DIR)/scripts

VIVADO_BATCH := $(VIVADO) -mode batch -nojournal

# 每個 target 結束都印大型 PASS/WARN/FAIL 圖示。
# 指令的 exit code 由 with_banner.sh 原樣傳遞 —— banner 只負責報告，不做決定。
BANNER      := $(PY)/banner.py
WITH_BANNER := VRA_PYTHON="$(PYTHON)" VRA_BANNER="$(BANNER)" \
               sh $(SCRIPTS)/with_banner.sh

# 組出各腳本共用的參數
PROJECT_ARGS := -project $(PROJECT) -outdir $(OUTDIR) -python $(PYTHON)
AUDIT_ARGS   := $(PROJECT_ARGS) -run $(RUN) -repo-root $(REPO_ROOT) -jobs $(JOBS) \
                $(foreach d,$(RTL_DIRS),-rtl-dir $(d)) \
                $(foreach d,$(XDC_DIRS),-xdc-dir $(d)) \
                $(foreach p,$(EXCLUDE),-exclude $(p)) \
                $(if $(wildcard $(WAIVERS)),-waivers $(WAIVERS),)

STAGES ?= synth_1 $(RUN)

# 各階段共用 OUTDIR，平行執行會互相覆寫；而且 check 靠順序達成 fail-fast。
.NOTPARALLEL:

.PHONY: help check-env check-files check-project elaborate synth impl \
        bitstream signoff check all test clean gen-ip \
        require-project require-filelist require-sram-gen

# -----------------------------------------------------------------------------

help:
	@echo "vivado-report-analyze-flow —— 各階段可單獨執行"
	@echo ""
	@echo "  make check-env      階段 0  工具與環境版本基準線        [需要 Vivado]"
	@echo "  make check-files    階段 1  靜態檔案檢查                [不需 Vivado]"
	@echo "  make check-project  階段 2  .xpr 專案稽核               [需要 Vivado]"
	@echo "  make elaborate      階段 3  Elaboration 預檢            [需要 Vivado]"
	@echo "  make synth          階段 4  合成 + 分析                 [需要 Vivado]"
	@echo "  make impl           階段 5  實作 + 分析                 [需要 Vivado]"
	@echo "  make bitstream      階段 6  產生 bitstream              [需要 Vivado]"
	@echo "  make signoff        階段 7  人類簽核報告                [不需 Vivado]"
	@echo ""
	@echo "  make gen-ip         用 SRAM_GEN_TCL 補產缺少的 IP      [需要 Vivado，分鐘級]"
	@echo ""
	@echo "  make check          階段 0-3（0-2 全部跑完後才 gate 階段 3）"
	@echo "  make all            階段 0-7 完整流程"
	@echo "  make test           本工具自己的測試                    [不需 Vivado]"
	@echo "  make clean          清除分析輸出"
	@echo ""
	@echo "設定來自 config.mk（可從 config.mk.example 複製）："
	@echo "  PROJECT   = $(PROJECT)"
	@echo "  RTL_DIRS  = $(RTL_DIRS)"
	@echo "  XDC_DIRS  = $(XDC_DIRS)"
	@echo "  FILELIST  = $(FILELIST)"
	@echo "  OUTDIR    = $(OUTDIR)"
	@echo "  RUN       = $(RUN)"

require-project:
	@if [ -z "$(PROJECT)" ]; then \
	  echo "錯誤：尚未設定 PROJECT。"; \
	  echo "請執行 'cp $(FLOW_DIR)/config.mk.example config.mk' 後編輯它。"; \
	  exit 2; \
	fi
	@if [ ! -f "$(PROJECT)" ]; then \
	  echo "錯誤：找不到專案檔 $(PROJECT)"; exit 2; \
	fi

require-filelist:
	@if [ -z "$(FILELIST)" ]; then \
	  echo "錯誤：尚未設定 FILELIST（要檢查的 .f / .tcl / .txt 清單）。"; \
	  exit 2; \
	fi

require-sram-gen:
	@if [ -z "$(SRAM_GEN_TCL)" ]; then \
	  echo "錯誤：尚未設定 SRAM_GEN_TCL（你自己的 IP 產生腳本）。"; \
	  echo "在 config.mk 指向它，例如 SRAM_GEN_TCL = tcl/gen_sram_checkdouble2.tcl"; \
	  exit 2; \
	fi
	@if [ ! -f "$(SRAM_GEN_TCL)" ]; then \
	  echo "錯誤：找不到 $(SRAM_GEN_TCL)"; exit 2; \
	fi

# --- 階段 0 ------------------------------------------------------------------

check-env:
	@echo "== 階段 0：工具與環境版本 =="
	@$(WITH_BANNER) "階段 0：工具與環境版本" "$(OUTDIR)/risk_env.json" -- \
	  $(VIVADO_BATCH) -source $(TCL)/check_environment.tcl \
	  -tclargs -outdir $(OUTDIR) -python $(PYTHON)

# --- 階段 1（不需要 Vivado）--------------------------------------------------

# PROJECT 是選填的：沒設也能檢查檔案是否存在，只是「與專案是否一致」會被
# 標示為未檢查，而不是靜靜跳過。.xpr 直接當 XML 讀，所以仍然不需要 Vivado。
FILELIST_ARGS := --outdir $(OUTDIR) \
                 $(foreach f,$(FILELIST),--filelist $(f)) \
                 $(if $(wildcard $(PROJECT)),--project $(PROJECT),) \
                 $(if $(wildcard $(OUTDIR)/manifests/filelist_$(RUN)_sources.txt),\
                    --fileset-list $(OUTDIR)/manifests/filelist_$(RUN)_sources.txt,) \
                 $(foreach d,$(RTL_DIRS),--search-dir $(d)) \
                 $(foreach m,$(IGNORE_MODULES),--ignore-module $(m))

check-files: require-filelist
	@echo "== 階段 1：靜態檔案檢查（不需要 Vivado）=="
	@$(WITH_BANNER) "階段 1：靜態檔案檢查" "$(OUTDIR)/risk_files.json" -- \
	  $(PYTHON) $(PY)/filelist.py $(FILELIST_ARGS)

# --- 階段 2 ------------------------------------------------------------------

check-project: require-project
	@echo "== 階段 2：專案稽核 =="
	@$(WITH_BANNER) "階段 2：專案稽核" "" -- \
	  $(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl \
	  -tclargs $(AUDIT_ARGS) -check-only

# --- 階段 3 ------------------------------------------------------------------

# ELABORATE_ARGS 可覆寫。`make check` 會另外帶 -fail-on-blocker：
# 手動跑單一階段維持寬鬆，串成 pre-check 時嚴格。
ELABORATE_ARGS ?=

elaborate: require-project
	@echo "== 階段 3：Elaboration 預檢 =="
	@$(WITH_BANNER) "階段 3：Elaboration 預檢" "$(OUTDIR)/risk_elaborate.json" -- \
	  $(VIVADO_BATCH) -log elaborate.log -source $(TCL)/elaborate_check.tcl \
	  -tclargs $(PROJECT_ARGS) $(ELABORATE_ARGS) \
	  $(if $(wildcard $(WAIVERS)),-waivers $(WAIVERS),)

# --- 階段 4-5 ----------------------------------------------------------------

# 只跑到合成為止（-run 指向 synth run），完成後就做一次分析。
synth: require-project
	@echo "== 階段 4：合成 + 分析 =="
	@$(WITH_BANNER) "階段 4：合成 + 分析" "$(OUTDIR)/risk_synth_1.json" -- \
	  $(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl \
	  -tclargs $(subst -run $(RUN),-run synth_1,$(AUDIT_ARGS))

impl: require-project
	@echo "== 階段 5：實作 + 分析 =="
	@$(WITH_BANNER) "階段 5：實作 + 分析" "$(OUTDIR)/risk_$(RUN).json" -- \
	  $(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl -tclargs $(AUDIT_ARGS)

# --- 階段 6 ------------------------------------------------------------------

bitstream: require-project
	@echo "== 階段 6：產生 bitstream =="
	@$(WITH_BANNER) "階段 6：產生 bitstream" "$(OUTDIR)/risk_$(RUN).json" -- \
	  $(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl \
	  -tclargs $(AUDIT_ARGS) -write-bitstream

# --- 階段 7（不需要 Vivado）--------------------------------------------------

signoff:
	@echo "== 階段 7：人類簽核報告 =="
	@$(WITH_BANNER) "階段 7：人類簽核報告" "" -- \
	  $(PYTHON) $(PY)/signoff_report.py --outdir $(OUTDIR) --stages $(STAGES)

# --- 補產缺少的 IP -----------------------------------------------------------

# 預設不掛進 check / all。那支腳本的 IP 組態是寫死的（單埠、無 byte write
# enable、輸出不註冊），只憑模組名稱自動產生有機會生出「名稱對、組態錯」的 IP
# —— elaboration 會過，上板行為卻是錯的，比原本的失敗更難查。
# 要自動化就在 config.mk 設 GEN_IP_AUTO = 1。
gen-ip: require-project require-sram-gen
	@echo "== 補產缺少的 IP（使用 $(SRAM_GEN_TCL)）=="
	@$(WITH_BANNER) "補產缺少的 IP" "" -- \
	  $(VIVADO_BATCH) -source $(TCL)/generate_missing_ip.tcl \
	  -tclargs $(PROJECT_ARGS) -script $(abspath $(SRAM_GEN_TCL)) \
	  -specs $(OUTDIR)/manifests/missing_ip_specs.txt -jobs $(JOBS)

# --- 組合 --------------------------------------------------------------------

# 階段 0-2 全部跑完再一次報齊（三者都是秒級，第一個失敗就停只會讓人來回跑），
# 階段 3 是分鐘級，才依彙總後的風險判定 gate。詳見 scripts/precheck.sh。
check:
	@VRA_MAKE="$(MAKE) -f $(FLOW_DIR)/Makefile" \
	 VRA_PYTHON="$(PYTHON)" VRA_PRECHECK="$(PY)/precheck.py" \
	 VRA_BANNER="$(BANNER)" \
	 VRA_OUTDIR="$(OUTDIR)" VRA_RUN="$(RUN)" VRA_WAIVERS="$(WAIVERS)" \
	 VRA_GEN_IP_AUTO="$(GEN_IP_AUTO)" \
	 ELABORATE_ARGS="-fail-on-blocker" \
	 sh $(SCRIPTS)/precheck.sh

all:
	@$(MAKE) -f $(FLOW_DIR)/Makefile --no-print-directory check
	@$(MAKE) -f $(FLOW_DIR)/Makefile --no-print-directory impl
	@$(MAKE) -f $(FLOW_DIR)/Makefile --no-print-directory signoff
	@$(PYTHON) $(PY)/banner.py --status 0 --label "完整流程" \
	  --verdict $(OUTDIR)/risk_$(RUN).json

# --- 工具本身 ----------------------------------------------------------------

test:
	@$(WITH_BANNER) "工具自身測試" "" -- sh $(FLOW_DIR)/tests/run_tests.sh

verify-reports:
	$(PYTHON) $(PY)/check_reports.py --dir $(OUTDIR)/raw --stage $(RUN)

clean:
	@echo "清除 $(OUTDIR)"
	@rm -rf $(OUTDIR)
