#!/bin/sh
# Run every check that does not need Vivado or a licence.
#
#   sh tests/run_tests.sh
#
# Set PYTHON=... to test against the interpreter you will actually use on the
# workstation, e.g. the one bundled with Vivado:
#   PYTHON=$XILINX_VIVADO/tps/lnx64/python-3.8.3/bin/python3 sh tests/run_tests.sh

set -e

REPO=$(cd "$(dirname "$0")/.." && pwd)
PYTHON=${PYTHON:-python3}
TCLSH=${TCLSH:-tclsh}
WORK=${WORK:-/tmp/vra-tests-$$}

cd "$REPO"
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

echo "== python unit tests ($PYTHON) =="
"$PYTHON" -m unittest discover -s tests -p 'test_*.py' -v

echo
echo "== Makefile targets that need no Vivado =="
MK="$WORK/mk"
mkdir -p "$MK/rtl"
echo 'module top; endmodule' > "$MK/rtl/top.v"
printf 'rtl/top.v\nrtl/absent.v\n' > "$MK/files.f"
cat > "$MK/config.mk" <<CONF
FILELIST = files.f
OUTDIR   = timing_analysis
PYTHON   = $PYTHON
CONF

run_make() {
    directory=$1
    shift
    set +e
    ( cd "$directory" && make -f "$REPO/Makefile" "$@" >make.log 2>&1 )
    status=$?
    set -e
    echo "$status"
}

# check-files must run and must fail on the missing file, with no Vivado.
status=$(run_make "$MK" check-files)
[ "$status" != "0" ] || fail "make check-files: should fail on a missing file"
grep -q "MISSING" "$MK/make.log" || fail "make check-files: did not report the gap"
echo "  ok  make check-files -> ran without Vivado, caught the missing file"

# The banner reports; it must never decide. If it swallowed the exit status,
# `make check` would stop failing fast and `make all` would keep going through
# a broken stage -- with everything still looking green.
grep -q "FAIL" "$MK/make.log" \
    || fail "banner: a failing target must print FAIL"
echo "  ok  banner -> FAIL shown, exit status still propagated"

# ...and the same wrapper must not turn a success into a failure. This one is
# genuinely clean: a project to reconcile against, so nothing reads as
# unchecked.
mkdir -p "$MK/ok/rtl" "$MK/ok/build"
echo 'module top; endmodule' > "$MK/ok/rtl/top.v"
printf 'rtl/top.v\n' > "$MK/ok/files.f"
cat > "$MK/ok/build/top.xpr" <<'XPR'
<?xml version="1.0" encoding="UTF-8"?>
<Project Version="7" Path="$PPRDIR/top.xpr">
  <FileSets Version="1">
    <FileSet Name="sources_1" Type="DesignSrcs">
      <File Path="$PPRDIR/../rtl/top.v"/>
      <Config>
        <Option Name="TopModule" Val="top"/>
      </Config>
    </FileSet>
  </FileSets>
</Project>
XPR
cat > "$MK/ok/config.mk" <<CONF
FILELIST = files.f
PROJECT  = build/top.xpr
OUTDIR   = timing_analysis
PYTHON   = $PYTHON
CONF
status=$(run_make "$MK/ok" check-files)
[ "$status" = "0" ] || fail "banner: a clean run must still exit 0, got $status"
grep -q "PASS" "$MK/ok/make.log" || fail "banner: a clean run must print PASS"
echo "  ok  banner -> PASS shown, clean run still exits 0"

# A stage whose problems never reach its exit code (stage 0 and 1 both grade
# CRITICAL without failing) must not be painted green by the banner.
"$PYTHON" - "$MK/ok/timing_analysis" <<'PY'
import json, sys
path = sys.argv[1] + "/risk_files.json"
data = json.load(open(path, encoding="utf-8"))
data["verdict"].update({"bringup_ok": False, "blockers": 1,
                        "bringup_blocker_count": 1})
data["verdict"]["counts"]["BLOCKER"] = 1
json.dump(data, open(path, "w", encoding="utf-8"))
PY
"$PYTHON" python/banner.py --status 0 --ascii --no-color \
    --label "階段 1" --verdict "$MK/ok/timing_analysis/risk_files.json" \
    > "$WORK/banner.log" || true
grep -q "指令以狀態" "$WORK/banner.log" \
    && fail "banner: graded on exit status instead of the verdict"
grep -q "不可上板" "$WORK/banner.log" \
    || fail "banner: exit 0 with a BLOCKER must still read as unsafe"
echo "  ok  banner -> exit 0 with a BLOCKER still reads FAIL"

# signoff must work from whatever artifacts exist, again with no Vivado.
run_make "$MK" signoff STAGES=impl_1 >/dev/null
[ -f "$MK/timing_analysis/signoff_latest.md" ] \
    || fail "make signoff: no report produced"
grep -q "NOT CHECKED" "$MK/timing_analysis/signoff_latest.md" \
    || fail "make signoff: stages that never ran must read as NOT CHECKED"
echo "  ok  make signoff -> report written, unrun stages marked NOT CHECKED"

# `make check` must drive stages 0-2 itself and gate stage 3. VIVADO=false
# makes the Vivado-dependent stages fail deterministically here and on a real
# workstation alike, so this test never launches a tool.
cat > "$MK/ok/config.mk" <<CONF
FILELIST = files.f
PROJECT  = build/top.xpr
OUTDIR   = timing_analysis
PYTHON   = $PYTHON
VIVADO   = false
CONF
status=$(run_make "$MK/ok" check)
[ "$status" != "0" ] || fail "make check: should not pass when stage 0 fails"
# The sub-makes must keep the -f pointing at this Makefile; without it every
# stage dies with "No rule to make target" and the gate never sees any result.
grep -q "No rule to make target" "$MK/ok/make.log" \
    && fail "make check: sub-make lost -f and could not find its own targets"
[ -f "$MK/ok/timing_analysis/precheck_latest.md" ] \
    || fail "make check: no consolidated pre-check report"
grep -q "階段 1：靜態檔案檢查" "$MK/ok/make.log" \
    || fail "make check: stage 1 did not run after stage 0 failed"
grep -q "elaboration）不執行" "$MK/ok/make.log" \
    || fail "make check: did not gate elaboration"
echo "  ok  make check -> cheap stages all ran, elaboration gated"

# A target needing config must explain itself rather than fail obscurely.
status=$(run_make "$WORK" check-project)
[ "$status" != "0" ] || fail "make check-project: should refuse without config"
grep -q "尚未設定 PROJECT" "$WORK/make.log" \
    || fail "make check-project: missing config gave no clear message"
echo "  ok  make check-project -> clear error when config.mk is missing"

if ! command -v "$TCLSH" >/dev/null 2>&1; then
    echo "== skipping pre-flight tests: no $TCLSH on PATH =="
    echo "ALL TESTS PASSED (python only)"
    exit 0
fi

echo
echo "== pre-flight tests ($TCLSH) =="

# Each scenario asserts on the exit status and on whether the run was launched.
run_scenario() {
    scenario=$1
    dir=${2:-$WORK/$scenario}
    set +e
    TEST_PYTHON="$PYTHON" "$TCLSH" tests/test_preflight.tcl "$scenario" "$dir" \
        > "$WORK/$scenario.log" 2>&1
    status=$?
    set -e
    echo "$status"
}

expect_abort() {
    scenario=$1
    expected_message=$2
    status=$(run_scenario "$scenario")
    [ "$status" = "1" ] || fail "$scenario: expected exit 1, got $status"
    grep -q "PRE-FLIGHT FAILED" "$WORK/$scenario.log" \
        || fail "$scenario: did not abort"
    grep -q "$expected_message" "$WORK/$scenario.log" \
        || fail "$scenario: missing expected error '$expected_message'"
    grep -q "launch_runs" "$WORK/$scenario.log" \
        && fail "$scenario: launched the run despite a failed audit"
    echo "  ok  $scenario -> aborted before launching"
}

expect_abort missing_xdc  "XDC on disk but NOT in fileset"
expect_abort missing_rtl  "RTL on disk but NOT in fileset"
expect_abort disabled_xdc "constraint file is DISABLED"

status=$(run_scenario check_only)
[ "$status" = "0" ] || fail "check_only: expected exit 0, got $status"
grep -q "launch_runs" "$WORK/check_only.log" \
    && fail "check_only: built the design despite -check-only"
[ -f "$WORK/check_only/timing_analysis/manifests/manifest_impl_1_current.json" ] \
    && fail "check_only: moved the manifest baseline without building"
echo "  ok  check_only -> audited only, no build, baseline untouched"

status=$(run_scenario ok)
[ "$status" = "0" ] || fail "ok: expected exit 0, got $status"
grep -q "launch_runs impl_1" "$WORK/ok.log" || fail "ok: never launched the run"
grep -q "reset_run impl_1" "$WORK/ok.log" \
    || fail "ok: first tracked run should force a clean rebuild"
[ -f "$WORK/ok/timing_analysis/latest_impl_1.md" ] \
    || fail "ok: no summary produced"
grep -q "WNS (setup worst slack)" "$WORK/ok/timing_analysis/latest_impl_1.md" \
    || fail "ok: summary missing timing table"
echo "  ok  ok -> audited, launched, summarised"

# Re-run against the very same tree (same paths, same content): the manifest
# must recognise it and skip the rebuild.
status=$(run_scenario rerun "$WORK/ok")
[ "$status" = "0" ] || fail "rerun: expected exit 0, got $status"
grep -q "inputs identical to previous run" "$WORK/rerun.log" \
    || fail "rerun: did not detect unchanged inputs"
grep -q "reset_run" "$WORK/rerun.log" \
    && fail "rerun: reset an up-to-date run with unchanged inputs"
echo "  ok  rerun -> unchanged inputs, no needless rebuild"

# The canned reports contain CDC-1, NSTD-1/UCIO-1 and TIMING-6, so the risk
# engine must grade this build as unsafe to take to the bench.
status=$(run_scenario blocker)
[ "$status" = "0" ] || fail "blocker: expected exit 0 without the gate flag, got $status"
RISK="$WORK/blocker/timing_analysis/risk_impl_1.md"
SUMMARY="$WORK/blocker/timing_analysis/latest_impl_1.md"
[ -f "$RISK" ] || fail "blocker: no risk report produced"
grep -q "驗證風險判定" "$SUMMARY" || fail "blocker: summary has no verdict block"
grep -q "可否上板 bring-up | ❌" "$SUMMARY" \
    || fail "blocker: bring-up should be blocked"
grep -q "CDC-1" "$SUMMARY" || fail "blocker: CDC finding missing from summary"
grep -q "NSTD-1" "$SUMMARY" || fail "blocker: DRC finding missing from summary"
echo "  ok  blocker -> graded unsafe, still exits 0 by default"

status=$(run_scenario blocker_gated)
[ "$status" = "1" ] || fail "blocker_gated: expected exit 1, got $status"
grep -q "fail-on-blocker" "$WORK/blocker_gated.log" \
    || fail "blocker_gated: did not report why it failed"
echo "  ok  blocker_gated -> -fail-on-blocker exits non-zero"

# A report that fails to generate must read as "not checked", never as clean.
status=$(run_scenario missing_report)
[ "$status" = "0" ] || fail "missing_report: expected exit 0, got $status"
MISSING_SUMMARY="$WORK/missing_report/timing_analysis/latest_impl_1.md"
grep -q "未檢查的項目" "$MISSING_SUMMARY" \
    || fail "missing_report: unavailable CDC report not surfaced"
grep -q "CDC" "$MISSING_SUMMARY" || fail "missing_report: CDC gap not named"
echo "  ok  missing_report -> unavailable analysis reported as a gap"

# Synthesis is analysed before implementation starts, and both roll up into a
# single overview.
status=$(run_scenario synth_stage)
[ "$status" = "0" ] || fail "synth_stage: expected exit 0, got $status"
SYNTH_DIR="$WORK/synth_stage/timing_analysis"
[ -f "$SYNTH_DIR/latest_synth_1.md" ] || fail "synth_stage: no synth summary"
[ -f "$SYNTH_DIR/latest_impl_1.md" ] || fail "synth_stage: no impl summary"
[ -f "$SYNTH_DIR/latest_flow.md" ] || fail "synth_stage: no flow overview"
grep -q "synth_1" "$SYNTH_DIR/latest_flow.md" \
    || fail "synth_stage: overview does not mention the synth stage"
# The synth stage must grade timing as a pre-placement estimate. The same WNS
# is a CRITICAL at impl and only a WARNING here, so it lands in the risk report
# rather than the compact summary -- which is itself the thing being asserted.
grep -q "合成後 setup" "$SYNTH_DIR/risk_synth_1.md" \
    || fail "synth_stage: timing not graded as a post-synthesis estimate"
grep -q "Setup 違規：WNS" "$SYNTH_DIR/risk_impl_1.md" \
    || fail "synth_stage: impl stage should grade the same slack as a result"
echo "  ok  synth_stage -> both stages analysed, overview written"

status=$(run_scenario synth_gated)
[ "$status" = "1" ] || fail "synth_gated: expected exit 1, got $status"
grep -q "stop-on-synth-blocker" "$WORK/synth_gated.log" \
    || fail "synth_gated: did not say why it stopped"
grep -q "launch_runs impl_1" "$WORK/synth_gated.log" \
    && fail "synth_gated: ran implementation despite a synth blocker"
echo "  ok  synth_gated -> stopped before implementation"

echo
echo "== IP generation wrapper ($TCLSH) =="

run_gen_ip() {
    scenario=$1
    set +e
    "$TCLSH" tests/test_gen_ip.tcl "$scenario" "$WORK/genip_$scenario" \
        > "$WORK/genip_$scenario.log" 2>&1
    status=$?
    set -e
    echo "$status"
}

status=$(run_gen_ip ok)
[ "$status" = "0" ] || fail "gen_ip ok: expected exit 0, got $status"
grep -q "create_ip blk_mem_gen_1216x80" "$WORK/genip_ok.log" \
    || fail "gen_ip ok: the detected spec never reached the generator"
# The stand-in script defaults to 2048x8 when nothing is injected. Seeing that
# name would mean the guard did not take and we generated the wrong memory.
grep -q "blk_mem_gen_2048x8" "$WORK/genip_ok.log" \
    && fail "gen_ip ok: used the script's default list, not the detected specs"
echo "  ok  gen_ip ok -> detected spec fed to the project's own script"

# "The script exited cleanly" is not "the IP exists". Verifying afterwards is
# the whole reason this wrapper exists rather than a bare `source`.
status=$(run_gen_ip nogen)
[ "$status" = "1" ] || fail "gen_ip nogen: expected exit 1, got $status"
grep -q "仍然不存在" "$WORK/genip_nogen.log" \
    || fail "gen_ip nogen: silently accepted a generator that produced nothing"
echo "  ok  gen_ip nogen -> clean exit from the script is not accepted as done"

status=$(run_gen_ip broken)
[ "$status" = "1" ] || fail "gen_ip broken: expected exit 1, got $status"
grep -q "產生腳本執行失敗" "$WORK/genip_broken.log" \
    || fail "gen_ip broken: did not name the failing script"
echo "  ok  gen_ip broken -> generator failure reported against its own script"

status=$(run_gen_ip empty)
[ "$status" = "0" ] || fail "gen_ip empty: expected exit 0, got $status"
grep -q "open_project" "$WORK/genip_empty.log" \
    && fail "gen_ip empty: opened the project with nothing to do"
echo "  ok  gen_ip empty -> nothing to generate, project left alone"

echo
echo "ALL TESTS PASSED"
