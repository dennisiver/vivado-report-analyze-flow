# -----------------------------------------------------------------------------
# timing_report_hooks.tcl
#
# Generates the raw Vivado timing report and hands it to the Python summariser.
# Sourced by preflight_and_run.tcl after a run completes, but deliberately
# independent of it so a non-project build script can call the same proc.
#
# Requires a design in memory (open_run / route_design already done).
#
#   source tcl/timing_report_hooks.tcl
#   vra::run_timing_report_and_analyze -stage impl_1 -outdir <dir>
# -----------------------------------------------------------------------------

namespace eval ::vra {
    variable hooks_dir [file normalize [file dirname [info script]]]
    variable repo_root [file dirname $hooks_dir]

    # Extra reports feeding the risk assessment: kind -> Vivado command.
    # The kind doubles as the report filename stem, which is what lets
    # analyze_run.py find them.
    variable report_commands [list \
        utilization       [list report_utilization] \
        drc               [list report_drc] \
        methodology       [list report_methodology] \
        cdc               [list report_cdc -details] \
        clock_interaction [list report_clock_interaction] \
        control_sets      [list report_control_sets -verbose]]

    variable default_reports {
        utilization drc methodology cdc clock_interaction control_sets
    }

    # Number of BLOCKER-level risk findings from the most recent analysis,
    # published here so preflight_and_run.tcl can gate on it.
    variable blockers 0
}

# Locate a Python 3 interpreter without assuming the workstation has one on
# PATH: Vivado ships its own, which is always present on an offline machine.
proc ::vra::find_python {{explicit ""}} {
    if {$explicit ne ""} {
        return $explicit
    }

    foreach candidate {python3 python3.8 python3.6} {
        set found [auto_execok $candidate]
        if {[llength $found]} {
            return [lindex $found 0]
        }
    }

    if {[info exists ::env(XILINX_VIVADO)]} {
        foreach pattern [list \
                [file join $::env(XILINX_VIVADO) tps lnx64 python-3* bin python3] \
                [file join $::env(XILINX_VIVADO) tps lnx64 python* bin python3]] {
            set hits [lsort -decreasing [glob -nocomplain $pattern]]
            if {[llength $hits]} {
                return [lindex $hits 0]
            }
        }
    }

    error "no python3 found. Pass -python <path>, or see README for the\
           interpreter bundled with Vivado under \$XILINX_VIVADO/tps/lnx64."
}

# Run a python helper from this repo, echoing its output into the Vivado log.
# Returns the captured output; raises on a non-zero exit.
proc ::vra::run_python {python script args} {
    variable repo_root
    set command [list $python [file join $repo_root python $script]]
    foreach argument $args {
        lappend command $argument
    }

    # 2>@1 matters: Tcl's exec raises an error whenever the child writes to
    # stderr, even when it exited successfully, so the streams are merged and
    # the real exit status is what decides success.
    set code [catch {exec {*}$command 2>@1} output]
    if {$output ne ""} {
        puts $output
    }
    if {$code} {
        error "python helper failed: $script\n$output"
    }
    return $output
}

proc ::vra::_parse_options {defaults_var args_list} {
    upvar 1 $defaults_var options
    foreach {key value} $args_list {
        if {![info exists options($key)]} {
            error "unknown option '$key'. Valid: [lsort [array names options]]"
        }
        set options($key) $value
    }
}

# -----------------------------------------------------------------------------
# Produce the timing report for the design currently in memory and summarise it.
#
#   -stage      run name the report belongs to (used in output filenames)
#   -outdir     analysis directory; raw reports land in <outdir>/raw
#   -max-paths  worst paths Vivado should emit per clock group
#   -python     explicit interpreter path
#   -preflight  preflight result json to fold into the summary
#   -reports    which supporting reports to generate (default: all six)
# -----------------------------------------------------------------------------
proc ::vra::run_timing_report_and_analyze {args} {
    variable repo_root

    array set options [list \
        -stage      impl_1 \
        -outdir     "" \
        -max-paths  10 \
        -python     "" \
        -preflight  "" \
        -stage-kind impl \
        -logs       {} \
        -waivers    "" \
        -bitstream  "" \
        -reports    $::vra::default_reports]
    ::vra::_parse_options options $args

    if {$options(-outdir) eq ""} {
        error "-outdir is required"
    }

    set stage  $options(-stage)
    set outdir [file normalize $options(-outdir)]
    set rawdir [file join $outdir raw]
    file mkdir $rawdir

    set report [file join $rawdir "timing_summary_${stage}.rpt"]

    puts "vra: writing timing report -> $report"
    # -check_timing_verbose is what catches constraints that never got applied;
    # -max_paths bounds the report so the parser has a fixed cost.
    report_timing_summary \
        -max_paths $options(-max-paths) \
        -delay_type min_max \
        -check_timing_verbose \
        -report_unconstrained \
        -significant_digits 3 \
        -file $report

    set python [::vra::find_python $options(-python)]
    set arguments [list \
        --stage $stage \
        --timing-summary $report \
        --outdir $outdir \
        --repo-root $repo_root \
        --stage-kind $options(-stage-kind) \
        --no-auto-discover]
    if {$options(-preflight) ne ""} {
        lappend arguments --preflight $options(-preflight)
    }
    if {$options(-waivers) ne ""} {
        lappend arguments --waivers $options(-waivers)
    }
    if {$options(-bitstream) ne ""} {
        lappend arguments --bitstream $options(-bitstream)
    }
    foreach log $options(-logs) {
        lappend arguments --log $log
    }

    # Generate the supporting reports. Each one is isolated: a command that is
    # unavailable for this device or design state must not cost us the others.
    # The path is passed to the analyser either way -- a report that failed to
    # generate has to surface as "not checked", never as "nothing found".
    array set commands $::vra::report_commands
    foreach kind $options(-reports) {
        if {![info exists commands($kind)]} {
            puts "vra: unknown report '$kind', skipping"
            continue
        }
        set target [file join $rawdir "${kind}_${stage}.rpt"]

        # Remove any file left by an earlier run so a stale report can never be
        # mistaken for this one's output.
        file delete -force $target

        if {[catch {{*}$commands($kind) -file $target} message]} {
            puts "vra: $kind report skipped ($message)"
        }
        lappend arguments --[string map {_ -} $kind] $target
    }

    puts "vra: summarising with $python"
    set output [::vra::run_python $python analyze_run.py {*}$arguments]

    set ::vra::blockers 0
    if {[regexp {##RISK_BLOCKERS##\s+(\d+)} $output -> count]} {
        set ::vra::blockers $count
    }

    return [file join $outdir "latest_${stage}.md"]
}
