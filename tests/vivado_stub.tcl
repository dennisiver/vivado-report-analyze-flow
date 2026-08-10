# -----------------------------------------------------------------------------
# vivado_stub.tcl -- a minimal fake Vivado for exercising the pre-flight logic
# in a plain tclsh, so the fileset audit can be tested without a licence, a
# workstation, or an hour of synthesis.
#
# It models just enough of the project object model: filesets, file properties,
# runs, and the launch/wait lifecycle. Everything is driven by ::stub::* state
# that the test scripts set up before sourcing preflight_and_run.tcl.
# -----------------------------------------------------------------------------

namespace eval ::stub {
    variable fileset_files       ;# fileset name -> list of paths
    array set fileset_files {}

    variable file_props          ;# "path,PROPERTY" -> value
    array set file_props {}

    variable run_props           ;# "run,PROPERTY" -> value
    array set run_props {}

    variable runs {}
    variable designs {}
    variable log {}
    variable report_body ""

    variable report_bodies       ;# report kind -> canned sample file
    array set report_bodies {}

    variable ips {}              ;# IP instance names present in the project
}

proc ::stub::record {line} {
    variable log
    lappend log $line
    puts "STUB: $line"
}

proc ::stub::set_run {name args} {
    variable runs
    variable run_props
    if {$name ni $runs} { lappend runs $name }
    foreach {key value} $args {
        set run_props($name,$key) $value
    }
}

proc ::stub::add_files {fileset paths} {
    variable fileset_files
    if {![info exists fileset_files($fileset)]} {
        set fileset_files($fileset) {}
    }
    foreach path $paths {
        lappend fileset_files($fileset) [file normalize $path]
    }
}

# --- Vivado command surface --------------------------------------------------

proc open_project {path} { ::stub::record "open_project $path" }
proc close_project {}    { ::stub::record "close_project" }
proc close_design {}     { ::stub::record "close_design" }

# --- IP object model ---------------------------------------------------------
#
# Enough for generate_missing_ip.tcl: the wrapper asks whether an IP exists
# after the project's own generator script ran, so the stub has to be able to
# both answer "no" and be changed to "yes" by create_ip.

proc get_ips {args} {
    variable ::stub::ips
    set names [lsearch -all -inline -not $args -quiet]
    if {![llength $names]} { return $::stub::ips }
    set wanted [lindex $names 0]
    if {$wanted in $::stub::ips} { return $wanted }
    return {}
}

proc create_ip {args} {
    variable ::stub::ips
    set index [lsearch -exact $args -module_name]
    if {$index < 0} {
        error "create_ip without -module_name"
    }
    set name [lindex $args [expr {$index + 1}]]
    if {$name ni $::stub::ips} { lappend ::stub::ips $name }
    ::stub::record "create_ip $name"
}

proc create_ip_run {name} {
    ::stub::set_run "${name}_synth_1" PROGRESS "0%" STATUS "Not started"
    ::stub::record "create_ip_run $name"
}

proc set_property {args}            { ::stub::record "set_property" }
proc generate_target {args}         { ::stub::record "generate_target $args" }
proc export_ip_user_files {args}    { ::stub::record "export_ip_user_files" }

proc get_designs {args} { return {} }

proc get_runs {args} {
    variable ::stub::runs
    set names [lsearch -all -inline -not $args -quiet]
    if {![llength $names]} { return $::stub::runs }
    set wanted [lindex $names 0]
    if {$wanted in $::stub::runs} { return $wanted }
    return {}
}

proc get_filesets {name} { return $name }

proc get_files {args} {
    variable ::stub::fileset_files
    # form: get_files -quiet -of_objects <fileset>
    set index [lsearch -exact $args -of_objects]
    if {$index >= 0} {
        set fileset [lindex $args [expr {$index + 1}]]
        if {[info exists ::stub::fileset_files($fileset)]} {
            return $::stub::fileset_files($fileset)
        }
        return {}
    }
    # form: get_files -quiet <path>  -> echo it back if it is known anywhere
    set candidates [lsearch -all -inline -not $args -quiet]
    if {![llength $candidates]} { return {} }
    set wanted [file normalize [lindex $candidates 0]]
    foreach fileset [array names ::stub::fileset_files] {
        if {$wanted in $::stub::fileset_files($fileset)} { return $wanted }
    }
    return {}
}

proc get_property {args} {
    variable ::stub::run_props
    variable ::stub::file_props
    set positional [lsearch -all -inline -not $args -quiet]
    set property [lindex $positional 0]
    set object   [lindex $positional 1]

    if {[info exists ::stub::run_props($object,$property)]} {
        return $::stub::run_props($object,$property)
    }
    set key "[file normalize $object],$property"
    if {[info exists ::stub::file_props($key)]} {
        return $::stub::file_props($key)
    }
    return ""
}

proc reset_run {name} {
    ::stub::record "reset_run $name"
    set ::stub::run_props($name,PROGRESS) "0%"
    set ::stub::run_props($name,NEEDS_REFRESH) "0"
}

proc launch_runs {name args} { ::stub::record "launch_runs $name $args" }

proc current_run {args} { ::stub::record "current_run $args" }

proc current_fileset {args} { return sources_1 }

proc synth_design {args} {
    ::stub::record "synth_design $args"
    if {[info exists ::stub::elaborate_fails]} {
        error "elaboration failed (stub)"
    }
}

proc wait_on_run {name} {
    ::stub::record "wait_on_run $name"
    # Simulate a run that completes successfully unless the test says otherwise.
    if {![info exists ::stub::run_props($name,FAIL)]} {
        set ::stub::run_props($name,PROGRESS) "100%"
        set ::stub::run_props($name,STATUS) "Complete!"
    }
}

proc open_run {name} { ::stub::record "open_run $name" }

proc report_timing_summary {args} {
    ::stub::record "report_timing_summary"
    set index [lsearch -exact $args -file]
    set target [lindex $args [expr {$index + 1}]]
    file mkdir [file dirname $target]
    file copy -force $::stub::report_body $target
}

# The supporting reports all behave the same way: copy a canned sample into
# place, or fail loudly when the test wants to exercise the "report did not
# run" path. ::stub::report_bodies maps report kind -> source file; a kind that
# is absent from it makes the command raise, as an unavailable report would.
proc ::stub::emit_report {kind args} {
    variable report_bodies
    ::stub::record "report_$kind"
    if {![info exists report_bodies($kind)]} {
        error "report_$kind is not available for this design (stub)"
    }
    set index [lsearch -exact $args -file]
    if {$index < 0} {
        error "report_$kind called without -file"
    }
    set target [lindex $args [expr {$index + 1}]]
    file mkdir [file dirname $target]
    file copy -force $report_bodies($kind) $target
}

proc report_utilization      {args} { ::stub::emit_report utilization {*}$args }
proc report_drc              {args} { ::stub::emit_report drc {*}$args }
proc report_methodology      {args} { ::stub::emit_report methodology {*}$args }
proc report_cdc              {args} { ::stub::emit_report cdc {*}$args }
proc report_clock_interaction {args} { ::stub::emit_report clock_interaction {*}$args }
proc report_control_sets     {args} { ::stub::emit_report control_sets {*}$args }
proc report_ip_status        {args} { ::stub::emit_report ip_status {*}$args }
proc report_qor_assessment   {args} { ::stub::emit_report qor_assessment {*}$args }

# Stage 0 asks the tool for its own version.
proc version {args} {
    if {[lsearch -exact $args -short] >= 0} {
        return "2024.2"
    }
    return "Vivado v.2024.2 (64-bit)\nSW Build 5239630 on Fri Nov 08 22:34:34 MST 2024"
}
