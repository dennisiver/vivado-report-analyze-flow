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

echo
echo "ALL TESTS PASSED"
