# -----------------------------------------------------------------------------
# test_preflight.tcl -- exercise preflight_and_run.tcl against the fake Vivado.
#
#   tclsh tests/test_preflight.tcl <scenario> <workdir>
#
# Scenarios:
#   ok            every file on disk is in the right fileset  -> launches
#   missing_xdc   an edited .xdc was never added to constrs_1 -> must abort
#   missing_rtl   a new .v was never added to sources_1       -> must abort
#   disabled_xdc  the constraint file is present but disabled -> must abort
#   rerun         reuse an existing workdir without touching the files, so the
#                 manifest sees identical inputs -> must not reset_run
#   check_only    audit a healthy project but stop before building
#
# The scenario builds a small tree on disk, points the stub project at some
# subset of it, then runs the real pre-flight script and prints the outcome for
# the harness in run_tests.sh to assert against.
# -----------------------------------------------------------------------------

set here [file normalize [file dirname [info script]]]
set repo [file dirname $here]

set scenario [lindex $argv 0]
set workdir  [file normalize [lindex $argv 1]]

# "rerun" deliberately keeps the tree (and the manifest baseline) from the
# preceding run so that nothing looks changed.
if {$scenario ne "rerun"} {
    file delete -force $workdir
}
file mkdir [file join $workdir rtl]
file mkdir [file join $workdir constrs]
file mkdir [file join $workdir build]

proc write_file {path content} {
    file mkdir [file dirname $path]
    set handle [open $path w]
    puts $handle $content
    close $handle
    return [file normalize $path]
}

set rtl_top   [write_file [file join $workdir rtl top.v] "module top; endmodule"]
set rtl_extra [write_file [file join $workdir rtl extra.v] "module extra; endmodule"]
set xdc_main  [write_file [file join $workdir constrs main.xdc] "create_clock -period 5 clk"]
set xdc_new   [write_file [file join $workdir constrs new_io.xdc] "set_input_delay 1 -clock clk"]
set xpr       [write_file [file join $workdir build top.xpr] "fake project"]

source [file join $here vivado_stub.tcl]
set ::stub::report_body [file join $repo examples sample_timing_summary.rpt]

::stub::set_run synth_1 PROGRESS "100%" STATUS "synth_design Complete!" \
    CONSTRSET constrs_1 SRCSET sources_1 NEEDS_REFRESH 0 \
    DIRECTORY [file join $workdir build synth_1]
::stub::set_run impl_1 PROGRESS "100%" STATUS "route_design Complete!" \
    CONSTRSET constrs_1 PARENT synth_1 NEEDS_REFRESH 0 \
    DIRECTORY [file join $workdir build impl_1]

# Every scenario starts from a correctly-populated project, then breaks it.
::stub::add_files sources_1 [list $rtl_top $rtl_extra]
::stub::add_files constrs_1 [list $xdc_main $xdc_new]

switch -exact -- $scenario {
    ok - rerun - check_only {
        # nothing to break
    }
    missing_xdc {
        # The classic failure: new_io.xdc exists on disk but the project never
        # had it added, so Vivado would silently build without it.
        set ::stub::fileset_files(constrs_1) [list $xdc_main]
    }
    missing_rtl {
        set ::stub::fileset_files(sources_1) [list $rtl_top]
    }
    disabled_xdc {
        set ::stub::file_props($xdc_new,IS_ENABLED) 0
    }
    default {
        puts "unknown scenario: $scenario"
        exit 3
    }
}

set argv [list \
    -project $xpr \
    -run impl_1 \
    -rtl-dir [file join $workdir rtl] \
    -xdc-dir [file join $workdir constrs] \
    -outdir [file join $workdir timing_analysis] \
    -python [expr {[info exists ::env(TEST_PYTHON)] ? $::env(TEST_PYTHON) : "python3"}]]

if {$scenario eq "check_only"} {
    lappend argv -check-only
}

source [file join $repo tcl preflight_and_run.tcl]
