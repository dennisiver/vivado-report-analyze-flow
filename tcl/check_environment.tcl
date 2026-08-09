# -----------------------------------------------------------------------------
# check_environment.tcl
#
# Stage 0. Asks Vivado what version it is and writes it out, so the Python side
# can compare it against the baseline from the last run. Asking the tool is the
# only reliable source -- $XILINX_VIVADO can point somewhere other than the
# binary actually running, and a wrapper script can shadow either.
#
#   vivado -mode batch -source tcl/check_environment.tcl -tclargs -outdir <dir>
# -----------------------------------------------------------------------------

set script_dir [file normalize [file dirname [info script]]]
source [file join $script_dir timing_report_hooks.tcl]

set outdir ""
set python ""

for {set i 0} {$i < [llength $argv]} {incr i} {
    switch -exact -- [lindex $argv $i] {
        -outdir { set outdir [lindex $argv [incr i]] }
        -python { set python [lindex $argv [incr i]] }
        default {
            puts "ERROR: unknown option '[lindex $argv $i]'"
            exit 2
        }
    }
}

if {$outdir eq ""} {
    puts "ERROR: -outdir is required"
    exit 2
}
set outdir [file normalize $outdir]
file mkdir [file join $outdir manifests]

# `version -short` gives "2024.2"; the long form carries the build number and
# date, which is what actually distinguishes two installs of the same release.
set short ""
set full ""
catch {set short [version -short]}
catch {set full [version]}

set build ""
if {[regexp {Build\s+(\S+)} $full -> matched]} {
    set build "Build $matched"
}

set install ""
if {[info exists ::env(XILINX_VIVADO)]} {
    set install $::env(XILINX_VIVADO)
}

proc ::vra::json_escape_env {text} {
    return [string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $text]
}

set target [file join $outdir manifests vivado_version.json]
set handle [open $target w]
puts $handle "{"
puts $handle "  \"version\": \"[::vra::json_escape_env $short]\","
puts $handle "  \"build\": \"[::vra::json_escape_env $build]\","
puts $handle "  \"version_full\": \"[::vra::json_escape_env $full]\","
puts $handle "  \"install_root\": \"[::vra::json_escape_env $install]\""
puts $handle "}"
close $handle

puts "vra: Vivado $short $build"
puts "vra: wrote $target"

# Hand off to Python for OS/Python detection and the baseline comparison.
set interpreter [::vra::find_python $python]
::vra::run_python $interpreter environment.py \
    --outdir $outdir --vivado-info $target --save

exit 0
