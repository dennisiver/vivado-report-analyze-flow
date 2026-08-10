#!/bin/sh
# -----------------------------------------------------------------------------
# precheck.sh -- drive stages 0-2, consolidate, then gate stage 3.
#
# Stages 0, 1 and 2 all finish in seconds, so they ALL run even when an earlier
# one found something: stopping at the first problem would make the human come
# back N times for N problems. Stage 3 (elaboration) takes minutes, so that one
# is gated on the consolidated verdict.
#
# The gate is the risk verdict, not the exit status. The build stages
# deliberately exit 0 even with BLOCKERs (gating is opt-in via
# -fail-on-blocker so that adding risk grading could never silently change an
# existing script), so gating on status alone would let a design with a
# known-missing module spend five minutes proving it.
#
# Configured entirely through the environment, set by the Makefile:
#   VRA_MAKE VRA_PYTHON VRA_PRECHECK VRA_OUTDIR VRA_RUN VRA_WAIVERS
# -----------------------------------------------------------------------------

make_cmd=${VRA_MAKE:-make}
python=${VRA_PYTHON:-python3}
precheck=${VRA_PRECHECK:?VRA_PRECHECK is required}
outdir=${VRA_OUTDIR:-timing_analysis}
run=${VRA_RUN:-impl_1}

st_env=0
$make_cmd --no-print-directory check-env || st_env=$?

st_files=0
$make_cmd --no-print-directory check-files || st_files=$?

# Opt-in only. Generating an IP from nothing but its module name can produce a
# correctly-named, wrongly-configured core -- that elaborates fine and is wrong
# on the board, which is worse than the failure it saves. Default is to print
# the command and let a human decide.
if [ "${VRA_GEN_IP_AUTO:-0}" = "1" ] && \
   [ -s "$outdir/manifests/missing_ip_specs.txt" ]; then
    echo ""
    echo "== GEN_IP_AUTO=1：先補產缺少的 IP =="
    if $make_cmd --no-print-directory gen-ip; then
        st_files=0
        $make_cmd --no-print-directory check-files || st_files=$?
    else
        echo "補產失敗 —— 維持階段 1 原本的結果。"
    fi
fi

# Status 2 from stage 0 means the environment itself is unusable (no Vivado,
# bad arguments) -- stage 2 needs to open the project, so it cannot run. Say so
# rather than letting it fail again with a more confusing message.
if [ "$st_env" -ge 2 ]; then
    st_project=skipped
else
    st_project=0
    $make_cmd --no-print-directory check-project || st_project=$?
fi

set -- --outdir "$outdir" --run "$run" \
       --status "env=$st_env" \
       --status "files=$st_files" \
       --status "project=$st_project"
if [ -n "$VRA_WAIVERS" ] && [ -f "$VRA_WAIVERS" ]; then
    set -- "$@" --waivers "$VRA_WAIVERS"
fi

echo ""
echo "== 建置前檢查彙總 =="
"$python" "$precheck" "$@"
gate=$?

if [ -n "$VRA_BANNER" ] && [ -f "$VRA_BANNER" ]; then
    "$python" "$VRA_BANNER" --status "$gate" --label "建置前檢查（階段 0-2）" \
        --verdict "$outdir/precheck.json" || true
fi

if [ "$gate" -ne 0 ]; then
    echo ""
    echo "階段 3（elaboration）不執行 —— 先解決上面的問題再跑一次 'make check'。"
    echo "詳見 $outdir/precheck_latest.md"
    exit "$gate"
fi

$make_cmd --no-print-directory elaborate
