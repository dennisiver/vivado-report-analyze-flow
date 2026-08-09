# -----------------------------------------------------------------------------
# elaborate_check.tcl
#
# Stage 3. Elaborates the design without synthesising it. This costs a fraction
# of a synthesis run but catches the errors that static file checks cannot see:
# a module with no definition, a port width that does not match, a missing
# `include, a syntax error. Finding those here saves the rest of the run.
#
# The verdict comes from the log, not from a report -- elaboration produces no
# reports, and the interesting messages (unbound module, inferred latch) are
# exactly the ones vivado_log.py is built to grade.
#
#   vivado -mode batch -log elaborate.log -source tcl/elaborate_check.tcl \
#       -tclargs -project build/top.xpr -outdir timing_analysis
# -----------------------------------------------------------------------------

set script_dir [file normalize [file dirname [info script]]]
source [file join $script_dir timing_report_hooks.tcl]

set project ""
set outdir ""
set python ""
set run ""
set waivers ""
set fail_on_blocker 0

for {set i 0} {$i < [llength $argv]} {incr i} {
    switch -exact -- [lindex $argv $i] {
        -project          { set project [lindex $argv [incr i]] }
        -outdir           { set outdir  [lindex $argv [incr i]] }
        -python           { set python  [lindex $argv [incr i]] }
        -run              { set run     [lindex $argv [incr i]] }
        -waivers          { set waivers [lindex $argv [incr i]] }
        -fail-on-blocker  { set fail_on_blocker 1 }
        default {
            puts "ERROR: unknown option '[lindex $argv $i]'"
            exit 2
        }
    }
}

if {$project eq ""} {
    puts "ERROR: -project <project.xpr> is required"
    exit 2
}
set project [file normalize $project]
if {$outdir eq ""} {
    set outdir [file join [file dirname $project] timing_analysis]
}
set outdir [file normalize $outdir]
set rawdir [file join $outdir raw]
file mkdir $rawdir

puts "PREFLIGHT      : opening $project for elaboration"
open_project $project

if {$run ne "" && [llength [get_runs -quiet $run]]} {
    current_run [get_runs $run]
}

# The elaborated design is never written out; it exists only so the tool reads
# every source and reports what it cannot resolve.
set elaborate_failed 0
if {[catch {synth_design -rtl -name vra_elab_check} message]} {
    set elaborate_failed 1
    puts "PREFLIGHT-ERROR: elaboration failed: $message"
} else {
    puts "PREFLIGHT      : elaboration completed"
}

catch {close_design}

# Vivado's own session log holds the messages we care about. -log on the
# command line controls where it lands; fall back to the default name.
set logs {}
foreach candidate [list \
        [file normalize elaborate.log] \
        [file normalize vivado.log] \
        [file join [pwd] vivado.log]] {
    if {[file isfile $candidate] && [lsearch -exact $logs $candidate] < 0} {
        lappend logs $candidate
    }
}

if {![llength $logs]} {
    puts "PREFLIGHT-WARN : no Vivado log found to analyse"
}

set interpreter [::vra::find_python $python]
set arguments [list \
    --stage elaborate \
    --stage-kind synth \
    --outdir $outdir \
    --timing-summary /dev/null \
    --no-auto-discover]
foreach log $logs {
    lappend arguments --log $log
}
if {$waivers ne ""} {
    lappend arguments --waivers [file normalize $waivers]
}

# /dev/null parses as an empty report, which is correct here: elaboration has
# no timing to report, and the risk assessment runs off the log alone.
if {[catch {::vra::run_python $interpreter analyze_run.py {*}$arguments} out]} {
    puts "PREFLIGHT-WARN : elaboration analysis failed: $out"
} else {
    if {[regexp {##RISK_BLOCKERS##\s+(\d+)} $out -> count]} {
        set ::vra::blockers $count
    }
}

if {$elaborate_failed} {
    puts ""
    puts "==================================================================="
    puts " ELABORATION FAILED -- 設計無法完成 elaboration。"
    puts " 詳見上方訊息與 $outdir/latest_elaborate.md"
    puts "==================================================================="
    exit 1
}

if {$::vra::blockers > 0} {
    puts ""
    puts " ⚠ elaboration 階段有 $::vra::blockers 項 BLOCKER。"
    if {$fail_on_blocker} {
        exit 1
    }
}

exit 0
