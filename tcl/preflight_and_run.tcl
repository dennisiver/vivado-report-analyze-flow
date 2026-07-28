# -----------------------------------------------------------------------------
# preflight_and_run.tcl
#
# Single entry point that replaces a bare "launch_runs": it verifies the run is
# actually about to build what you think it is, then launches it and summarises
# the timing result.
#
# The failure this exists to prevent: an edited .xdc that was never added to
# constrs_1 is invisible to Vivado's own out-of-date detection (the file simply
# is not part of the design), so the run happily reports timing for constraints
# you are no longer using -- and you only find out an hour later.
#
#   vivado -mode batch -source tcl/preflight_and_run.tcl -tclargs \
#       -project build/top.xpr -run impl_1 \
#       -rtl-dir rtl -xdc-dir constrs -repo-root .
#
# Options:
#   -project <xpr>       required, the Vivado project
#   -run <name>          run to build, default impl_1
#   -rtl-dir <dir>       RTL directory to audit against sources_1 (repeatable)
#   -xdc-dir <dir>       XDC directory to audit against the run's constraint
#                        fileset (repeatable)
#   -outdir <dir>        analysis output, default <project dir>/timing_analysis
#   -repo-root <dir>     git tree holding the RTL, for commit provenance
#   -exclude <pattern>   skip disk files matching this glob (repeatable),
#                        e.g. -exclude "*/tb/*" for testbenches
#   -python <path>       explicit python3 interpreter
#   -jobs <n>            parallel jobs for launch_runs, default 4
#   -reports <list>      supporting reports to generate, default all six:
#                        utilization drc methodology cdc clock_interaction
#                        control_sets
#   -check-only          run the audit and exit without launching
#   -no-reset            never reset_run, even when the inputs changed
#   -warn-missing-rtl    treat RTL missing from the fileset as a warning
#   -fail-on-blocker     exit non-zero when the risk assessment finds a
#                        BLOCKER, for gating write_bitstream or a deploy step
# -----------------------------------------------------------------------------

set script_dir [file normalize [file dirname [info script]]]
source [file join $script_dir timing_report_hooks.tcl]

namespace eval ::vra {
    variable errors {}
    variable warnings {}
}

proc ::vra::err {message} {
    variable errors
    lappend errors $message
    puts "PREFLIGHT-ERROR: $message"
}

proc ::vra::warn {message} {
    variable warnings
    lappend warnings $message
    puts "PREFLIGHT-WARN : $message"
}

proc ::vra::info_line {message} {
    puts "PREFLIGHT      : $message"
}

# --- small JSON writer -------------------------------------------------------

proc ::vra::json_escape {text} {
    return [string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $text]
}

proc ::vra::json_array {items} {
    set parts {}
    foreach item $items {
        lappend parts "\"[::vra::json_escape $item]\""
    }
    return "\[[join $parts ,]\]"
}

# --- filesystem helpers ------------------------------------------------------

# Recursively collect files under $dir matching any of $patterns.
proc ::vra::find_files {dir patterns} {
    set found {}
    if {![file isdirectory $dir]} {
        return $found
    }
    foreach pattern $patterns {
        foreach path [glob -nocomplain -directory $dir -types f -- $pattern] {
            lappend found [file normalize $path]
        }
    }
    foreach sub [glob -nocomplain -directory $dir -types d -- *] {
        set found [concat $found [::vra::find_files $sub $patterns]]
    }
    return $found
}

proc ::vra::is_excluded {path patterns} {
    foreach pattern $patterns {
        if {[string match $pattern $path]} {
            return 1
        }
    }
    return 0
}

proc ::vra::normalize_all {paths} {
    set result {}
    foreach path $paths {
        lappend result [file normalize $path]
    }
    return $result
}

proc ::vra::write_lines {path lines} {
    file mkdir [file dirname $path]
    set handle [open $path w]
    foreach line $lines {
        puts $handle $line
    }
    close $handle
    return $path
}

# --- option parsing ----------------------------------------------------------

array set opts {
    project           ""
    run               impl_1
    outdir            ""
    repo_root         ""
    python            ""
    reports           ""
    jobs              4
    check_only        0
    no_reset          0
    warn_missing_rtl  0
    fail_on_blocker   0
}
set opts(rtl_dirs) {}
set opts(xdc_dirs) {}
set opts(exclude)  {}

for {set i 0} {$i < [llength $argv]} {incr i} {
    set flag [lindex $argv $i]
    switch -exact -- $flag {
        -project          { set opts(project)   [lindex $argv [incr i]] }
        -run              { set opts(run)       [lindex $argv [incr i]] }
        -outdir           { set opts(outdir)    [lindex $argv [incr i]] }
        -repo-root        { set opts(repo_root) [lindex $argv [incr i]] }
        -python           { set opts(python)    [lindex $argv [incr i]] }
        -jobs             { set opts(jobs)      [lindex $argv [incr i]] }
        -rtl-dir          { lappend opts(rtl_dirs) [lindex $argv [incr i]] }
        -xdc-dir          { lappend opts(xdc_dirs) [lindex $argv [incr i]] }
        -exclude          { lappend opts(exclude)  [lindex $argv [incr i]] }
        -reports          { set opts(reports)   [lindex $argv [incr i]] }
        -check-only       { set opts(check_only) 1 }
        -no-reset         { set opts(no_reset) 1 }
        -warn-missing-rtl { set opts(warn_missing_rtl) 1 }
        -fail-on-blocker  { set opts(fail_on_blocker) 1 }
        default {
            puts "ERROR: unknown option '$flag'"
            exit 2
        }
    }
}

if {$opts(project) eq ""} {
    puts "ERROR: -project <project.xpr> is required"
    exit 2
}

set project_file [file normalize $opts(project)]
if {![file exists $project_file]} {
    puts "ERROR: project not found: $project_file"
    exit 2
}

if {$opts(outdir) eq ""} {
    set opts(outdir) [file join [file dirname $project_file] timing_analysis]
}
set outdir [file normalize $opts(outdir)]
file mkdir $outdir

# -----------------------------------------------------------------------------
# 1. Open the project and resolve which filesets this run really consumes
# -----------------------------------------------------------------------------

::vra::info_line "opening $project_file"
open_project $project_file

set run_name $opts(run)
if {![llength [get_runs -quiet $run_name]]} {
    puts "ERROR: run '$run_name' does not exist. Available: [get_runs]"
    exit 2
}
set run [get_runs $run_name]

set parent_run ""
if {[catch {set parent_run [get_property -quiet PARENT $run]}]} {
    set parent_run ""
}

set constrset [get_property -quiet CONSTRSET $run]
set srcset    [get_property -quiet SRCSET $run]
if {$srcset eq "" && $parent_run ne ""} {
    set srcset [get_property -quiet SRCSET [get_runs $parent_run]]
}
if {$srcset eq ""}    { set srcset sources_1 }
if {$constrset eq ""} { set constrset constrs_1 }

::vra::info_line "run '$run_name' uses sources '$srcset' and constraints '$constrset'"

# A run whose constraint fileset differs from its synth parent's is a classic
# source of "my XDC had no effect" -- the two stages saw different constraints.
if {$parent_run ne ""} {
    set parent_constrset [get_property -quiet CONSTRSET [get_runs $parent_run]]
    if {$parent_constrset ne "" && $parent_constrset ne $constrset} {
        ::vra::warn "run '$run_name' uses constraint fileset '$constrset' but its\
                     synthesis parent '$parent_run' uses '$parent_constrset' --\
                     synthesis and implementation are constrained differently"
    }
}

set fileset_sources [::vra::normalize_all [get_files -quiet -of_objects [get_filesets $srcset]]]
set fileset_xdc {}
foreach file [get_files -quiet -of_objects [get_filesets $constrset]] {
    if {[string tolower [file extension $file]] in {.xdc .tcl .sdc}} {
        lappend fileset_xdc [file normalize $file]
    }
}

# -----------------------------------------------------------------------------
# 2. Audit: does the design on disk match the design in the project?
# -----------------------------------------------------------------------------

set rtl_design_patterns {*.v *.sv *.vhd *.vhdl}
set rtl_header_patterns {*.vh *.svh}

set disk_rtl {}
foreach dir $opts(rtl_dirs) {
    set disk_rtl [concat $disk_rtl \
        [::vra::find_files [file normalize $dir] \
            [concat $rtl_design_patterns $rtl_header_patterns]]]
}

set disk_xdc {}
foreach dir $opts(xdc_dirs) {
    set disk_xdc [concat $disk_xdc [::vra::find_files [file normalize $dir] {*.xdc}]]
}

# Files on disk that the project never learned about. For XDC this is always an
# error; for RTL a testbench directory can legitimately be absent, so it is
# downgradable via -warn-missing-rtl / -exclude.
foreach path $disk_xdc {
    if {[::vra::is_excluded $path $opts(exclude)]} { continue }
    if {$path ni $fileset_xdc} {
        ::vra::err "XDC on disk but NOT in fileset '$constrset': $path\
                    -- add it with: add_files -fileset $constrset $path"
    }
}

foreach path $disk_rtl {
    if {[::vra::is_excluded $path $opts(exclude)]} { continue }
    if {[string tolower [file extension $path]] in {.vh .svh}} {
        continue
    }
    if {$path ni $fileset_sources} {
        if {$opts(warn_missing_rtl)} {
            ::vra::warn "RTL on disk but not in fileset '$srcset': $path"
        } else {
            ::vra::err "RTL on disk but NOT in fileset '$srcset': $path\
                        -- add it, or skip it with -exclude/-warn-missing-rtl"
        }
    }
}

# Files the project references but which no longer exist.
foreach path [concat $fileset_sources $fileset_xdc] {
    if {![file exists $path]} {
        ::vra::err "file referenced by the project is missing on disk: $path"
    }
}

# Constraints present in the project but disabled or scoped to a single stage.
foreach path $fileset_xdc {
    set file_obj [get_files -quiet $path]
    if {![llength $file_obj]} { continue }

    if {[get_property -quiet IS_ENABLED $file_obj] eq "0"} {
        ::vra::err "constraint file is DISABLED in the project: $path"
    }
    foreach {property label} {USED_IN_SYNTHESIS synthesis
                              USED_IN_IMPLEMENTATION implementation} {
        if {[get_property -quiet $property $file_obj] eq "0"} {
            ::vra::warn "constraint file is not used in $label: $path"
        }
    }
}

if {![llength $fileset_xdc]} {
    ::vra::err "fileset '$constrset' contains no constraint files at all"
}

::vra::info_line "audited [llength $fileset_sources] source file(s),\
                  [llength $fileset_xdc] constraint file(s)"

# -----------------------------------------------------------------------------
# 3. Manifest: hash every input so staleness is detectable next time
# -----------------------------------------------------------------------------

# The manifest deliberately covers the union of fileset and on-disk files, so
# that include-only headers (never added to the fileset, but still compiled in)
# still register as a change.
set manifest_sources [lsort -unique [concat $fileset_sources $disk_rtl]]
set manifest_xdc     [lsort -unique [concat $fileset_xdc $disk_xdc]]

set list_dir [file join $outdir manifests]
set sources_list [::vra::write_lines \
    [file join $list_dir "filelist_${run_name}_sources.txt"] $manifest_sources]
set xdc_list [::vra::write_lines \
    [file join $list_dir "filelist_${run_name}_constraints.txt"] $manifest_xdc]

set python [::vra::find_python $opts(python)]
set manifest_args [list \
    --stage $run_name \
    --outdir $outdir \
    --sources-list $sources_list \
    --constraints-list $xdc_list]
if {$opts(repo_root) ne ""} {
    lappend manifest_args --repo-root [file normalize $opts(repo_root)]
}

# Compare only for now. The stored baseline must describe the last run that
# actually completed, otherwise a build that aborts here would poison the next
# comparison into reporting "nothing changed".
proc ::vra::manifest_step {python manifest_args save} {
    set arguments $manifest_args
    if {$save} {
        lappend arguments --save
    }
    if {[catch {::vra::run_python $python manifest.py {*}$arguments} output]} {
        ::vra::warn "manifest step failed, continuing without change detection: $output"
        return -1
    }
    if {[regexp {##MANIFEST_CHANGED##\s+(\S+)} $output -> changed]} {
        return [expr {$changed eq "yes"}]
    }
    return -1
}

set inputs_changed [::vra::manifest_step $python $manifest_args 0]
if {$inputs_changed < 0} {
    set inputs_changed 0
}

# -----------------------------------------------------------------------------
# 4. Record the audit result, then stop if anything is wrong
# -----------------------------------------------------------------------------

set preflight_json [file join $outdir manifests "preflight_${run_name}.json"]
set status [expr {[llength $::vra::errors] ? "FAIL" : "PASS"}]

set handle [open $preflight_json w]
puts $handle "{"
puts $handle "  \"status\": \"$status\","
puts $handle "  \"run\": \"[::vra::json_escape $run_name]\","
puts $handle "  \"srcset\": \"[::vra::json_escape $srcset]\","
puts $handle "  \"constrset\": \"[::vra::json_escape $constrset]\","
puts $handle "  \"inputs_changed\": [expr {$inputs_changed ? "true" : "false"}],"
puts $handle "  \"errors\": [::vra::json_array $::vra::errors],"
puts $handle "  \"warnings\": [::vra::json_array $::vra::warnings]"
puts $handle "}"
close $handle

::vra::info_line "audit $status ([llength $::vra::errors] error(s),\
                  [llength $::vra::warnings] warning(s)) -> $preflight_json"

if {[llength $::vra::errors]} {
    puts ""
    puts "==================================================================="
    puts " PRE-FLIGHT FAILED -- not launching '$run_name'."
    puts " Fix the errors above; nothing was built, so no time was wasted."
    puts "==================================================================="
    exit 1
}

if {$opts(check_only)} {
    ::vra::info_line "check-only requested, stopping before launch"
    exit 0
}

# -----------------------------------------------------------------------------
# 5. Launch, forcing a clean rebuild when the inputs actually changed
# -----------------------------------------------------------------------------

proc ::vra::run_is_complete {run_name} {
    return [expr {[get_property PROGRESS [get_runs $run_name]] eq "100%"}]
}

proc ::vra::ensure_run {run_name jobs force_reset} {
    set needs_refresh [get_property -quiet NEEDS_REFRESH [get_runs $run_name]]
    if {$force_reset || $needs_refresh eq "1" || ![::vra::run_is_complete $run_name]} {
        if {[::vra::run_is_complete $run_name] || $needs_refresh eq "1"} {
            ::vra::info_line "resetting run '$run_name'\
                              (inputs changed or Vivado flagged it stale)"
            reset_run $run_name
        }
        ::vra::info_line "launching run '$run_name'"
        launch_runs $run_name -jobs $jobs
        wait_on_run $run_name
    } else {
        ::vra::info_line "run '$run_name' already complete and inputs unchanged"
    }

    if {![::vra::run_is_complete $run_name]} {
        set directory [get_property DIRECTORY [get_runs $run_name]]
        puts "ERROR: run '$run_name' did not complete\
              (status: [get_property STATUS [get_runs $run_name]])."
        puts "       See the run log under: $directory"
        exit 1
    }
}

set force_reset [expr {$inputs_changed && !$opts(no_reset)}]
if {$force_reset} {
    ::vra::info_line "inputs changed since the last tracked run -- forcing a clean rebuild"
}

if {$parent_run ne ""} {
    ::vra::ensure_run $parent_run $opts(jobs) $force_reset
}
::vra::ensure_run $run_name $opts(jobs) $force_reset

# -----------------------------------------------------------------------------
# 6. Report and summarise
# -----------------------------------------------------------------------------

# The build succeeded, so this input set is now the baseline that future runs
# are compared against.
::vra::manifest_step $python $manifest_args 1

::vra::info_line "opening completed run '$run_name'"
if {[llength [get_designs -quiet]]} {
    close_design
}
open_run $run_name

set analyze_options [list \
    -stage $run_name \
    -outdir $outdir \
    -python $python \
    -preflight $preflight_json]
if {$opts(reports) ne ""} {
    lappend analyze_options -reports $opts(reports)
}
set summary [::vra::run_timing_report_and_analyze {*}$analyze_options]

puts ""
puts "==================================================================="
puts " Done. Read this file (and only this file) for the timing result:"
puts "   $summary"
puts "   Risk report: [file join $outdir risk_${run_name}.md]"
puts "==================================================================="

# The build itself succeeded, so the default exit status stays 0. Gating on
# risk is opt-in, to avoid silently breaking scripts that only expect a
# non-zero status when Vivado actually failed.
if {$::vra::blockers > 0} {
    puts ""
    puts " ⚠ 有 $::vra::blockers 項 BLOCKER —— 不建議在解決前拿這個 bitstream 上板驗證。"
    if {$opts(fail_on_blocker)} {
        puts " -fail-on-blocker 已啟用，以非 0 狀態結束。"
        exit 1
    }
}

exit 0
