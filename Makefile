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

PY  := $(FLOW_DIR)/python
TCL := $(FLOW_DIR)/tcl

VIVADO_BATCH := $(VIVADO) -mode batch -nojournal

# 組出各腳本共用的參數
PROJECT_ARGS := -project $(PROJECT) -outdir $(OUTDIR) -python $(PYTHON)
AUDIT_ARGS   := $(PROJECT_ARGS) -run $(RUN) -repo-root $(REPO_ROOT) -jobs $(JOBS) \
                $(foreach d,$(RTL_DIRS),-rtl-dir $(d)) \
                $(foreach d,$(XDC_DIRS),-xdc-dir $(d)) \
                $(foreach p,$(EXCLUDE),-exclude $(p)) \
                $(if $(wildcard $(WAIVERS)),-waivers $(WAIVERS),)

STAGES ?= synth_1 $(RUN)

.PHONY: help check-env check-files check-project elaborate synth impl \
        bitstream signoff check all test clean require-project require-filelist

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
	@echo "  make check          階段 0-3（所有建置前的檢查）"
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

# --- 階段 0 ------------------------------------------------------------------

check-env:
	@echo "== 階段 0：工具與環境版本 =="
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
	$(PYTHON) $(PY)/filelist.py $(FILELIST_ARGS)

# --- 階段 2 ------------------------------------------------------------------

check-project: require-project
	@echo "== 階段 2：專案稽核 =="
	$(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl \
	  -tclargs $(AUDIT_ARGS) -check-only

# --- 階段 3 ------------------------------------------------------------------

elaborate: require-project
	@echo "== 階段 3：Elaboration 預檢 =="
	$(VIVADO_BATCH) -log elaborate.log -source $(TCL)/elaborate_check.tcl \
	  -tclargs $(PROJECT_ARGS) \
	  $(if $(wildcard $(WAIVERS)),-waivers $(WAIVERS),)

# --- 階段 4-5 ----------------------------------------------------------------

# 只跑到合成為止（-run 指向 synth run），完成後就做一次分析。
synth: require-project
	@echo "== 階段 4：合成 + 分析 =="
	$(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl \
	  -tclargs $(subst -run $(RUN),-run synth_1,$(AUDIT_ARGS))

impl: require-project
	@echo "== 階段 5：實作 + 分析 =="
	$(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl -tclargs $(AUDIT_ARGS)

# --- 階段 6 ------------------------------------------------------------------

bitstream: require-project
	@echo "== 階段 6：產生 bitstream =="
	$(VIVADO_BATCH) -source $(TCL)/preflight_and_run.tcl \
	  -tclargs $(AUDIT_ARGS) -write-bitstream

# --- 階段 7（不需要 Vivado）--------------------------------------------------

signoff:
	@echo "== 階段 7：人類簽核報告 =="
	$(PYTHON) $(PY)/signoff_report.py --outdir $(OUTDIR) --stages $(STAGES)

# --- 組合 --------------------------------------------------------------------

check: check-env check-files check-project elaborate
	@echo ""
	@echo "建置前的檢查全部完成。接著可以執行 'make impl'。"

all:
	@$(MAKE) --no-print-directory check-env
	@$(MAKE) --no-print-directory check-files
	@$(MAKE) --no-print-directory check-project
	@$(MAKE) --no-print-directory elaborate
	@$(MAKE) --no-print-directory impl
	@$(MAKE) --no-print-directory signoff

# --- 工具本身 ----------------------------------------------------------------

test:
	@sh $(FLOW_DIR)/tests/run_tests.sh

verify-reports:
	$(PYTHON) $(PY)/check_reports.py --dir $(OUTDIR)/raw --stage $(RUN)

clean:
	@echo "清除 $(OUTDIR)"
	@rm -rf $(OUTDIR)
