# -----------------------------------------------------------------------------
# generate_missing_ip.tcl
#
# Feeds the specs detected by stage 1 into the project's OWN IP generator script
# and then verifies the IP actually appeared.
#
# This file deliberately contains no create_ip / set_property / generate_target
# of its own. The project already has a script that knows the right core
# version and the right CONFIG dictionary for its memories; a second, subtly
# different generator here would be a new way to produce a wrongly-configured
# IP that still elaborates. So: detect here, generate there, verify here.
#
# The generator script is sourced with `sram_specs` already set. A script that
# guards its own default with
#
#     if {![info exists sram_specs]} { set sram_specs { ... } }
#
# keeps working unchanged when run standalone and picks up the detected list
# when sourced from here.
#
#   vivado -mode batch -source tcl/generate_missing_ip.tcl -tclargs \
#       -project build/top.xpr -script tcl/gen_sram_checkdouble2.tcl \
#       -specs timing_analysis/manifests/missing_ip_specs.txt
#
# Options:
#   -project <xpr>   required
#   -script <tcl>    required, the project's own IP generator
#   -specs <file>    required, one <depth>x<width> per line (stage 1 writes it)
#   -outdir <dir>    analysis output, for the result manifest
#   -jobs <n>        passed through to the generator via ::vra::ip_jobs
#   -python <path>   unused here, accepted so the Makefile can pass one set of
#                    project arguments to every script
# -----------------------------------------------------------------------------

set script_dir [file normalize [file dirname [info script]]]
source [file join $script_dir timing_report_hooks.tcl]

set project ""
set generator ""
set specs_file ""
set outdir ""
set jobs 4

for {set i 0} {$i < [llength $argv]} {incr i} {
    switch -exact -- [lindex $argv $i] {
        -project { set project [lindex $argv [incr i]] }
        -script  { set generator [lindex $argv [incr i]] }
        -specs   { set specs_file [lindex $argv [incr i]] }
        -outdir  { set outdir [lindex $argv [incr i]] }
        -jobs    { set jobs [lindex $argv [incr i]] }
        -python  { incr i }
        default {
            puts "ERROR: unknown option '[lindex $argv $i]'"
            exit 2
        }
    }
}

foreach {name value} [list -project $project -script $generator \
                           -specs $specs_file] {
    if {$value eq ""} {
        puts "ERROR: $name is required"
        exit 2
    }
}

if {![file isfile $project]} {
    puts "ERROR: project not found: $project"
    exit 2
}
if {![file isfile $generator]} {
    puts "ERROR: generator script not found: $generator"
    exit 2
}

if {$outdir eq ""} {
    set outdir [file join [file dirname [file normalize $project]] \
                    timing_analysis]
}
set outdir [file normalize $outdir]
file mkdir [file join $outdir manifests]

# --- read the specs ----------------------------------------------------------

set sram_specs {}
if {[file isfile $specs_file]} {
    set handle [open $specs_file r]
    foreach line [split [read $handle] \n] {
        set line [string trim $line]
        if {$line eq "" || [string index $line 0] eq "#"} {
            continue
        }
        if {![regexp {^(\d+)x(\d+)$} $line]} {
            puts "GENIP-WARN : ignoring unrecognised spec '$line'"
            continue
        }
        if {[lsearch -exact $sram_specs $line] < 0} {
            lappend sram_specs $line
        }
    }
    close $handle
}

if {![llength $sram_specs]} {
    puts "GENIP      : 沒有需要補產的 IP（$specs_file 是空的）。"
    puts "GENIP      : 先跑 'make check-files' 產生清單。"
    exit 0
}

puts "GENIP      : 要補產 [llength $sram_specs] 個 IP：[join $sram_specs {, }]"

# --- run the project's own generator ----------------------------------------

open_project $project

set ::vra::ip_jobs $jobs

if {[catch {source $generator} generator_error]} {
    puts ""
    puts "GENIP-ERROR: 產生腳本執行失敗：$generator_error"
    puts "GENIP-ERROR: 腳本是 $generator"
    close_project
    exit 1
}

# --- verify, because "the script ran" is not "the IP exists" ------------------

proc ::vra::ip_state {name} {
    if {[llength [get_ips -quiet $name]] == 0} {
        return "missing"
    }
    set run_name "${name}_synth_1"
    if {[llength [get_runs -quiet $run_name]] == 0} {
        # No out-of-context run is not itself a failure: the IP can be built
        # as part of the top-level synthesis instead.
        return "ok"
    }
    set status [get_property STATUS [get_runs $run_name]]
    if {[string match -nocase "*error*" $status]} {
        return "run-failed"
    }
    return "ok"
}

set results {}
set failed 0
foreach spec $sram_specs {
    regexp {(\d+)x(\d+)} $spec -> depth width
    set name "blk_mem_gen_${depth}x${width}"
    set state [::vra::ip_state $name]
    lappend results [list $name $state]
    if {$state ne "ok"} {
        incr failed
    }
}

close_project

# --- report ------------------------------------------------------------------

proc ::vra::json_escape_ip {text} {
    return [string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $text]
}

set stamp [clock format [clock seconds] -format "%Y%m%d_%H%M%S"]
set target [file join $outdir manifests "gen_ip_${stamp}.json"]
set handle [open $target w]
puts $handle "{"
puts $handle "  \"generator\": \"[::vra::json_escape_ip $generator]\","
puts $handle "  \"failed\": $failed,"
puts $handle "  \"ips\": \["
set count [llength $results]
for {set i 0} {$i < $count} {incr i} {
    set entry [lindex $results $i]
    set comma [expr {$i == $count - 1 ? "" : ","}]
    puts $handle "    {\"name\": \"[lindex $entry 0]\", \
\"state\": \"[lindex $entry 1]\"}$comma"
}
puts $handle "  \]"
puts $handle "}"
close $handle

puts ""
foreach entry $results {
    puts "GENIP      : [lindex $entry 0] -> [lindex $entry 1]"
}
puts "GENIP      : wrote $target"

if {$failed > 0} {
    puts ""
    puts "GENIP-ERROR: $failed 個 IP 在腳本跑完後仍然不存在或合成失敗。"
    puts "GENIP-ERROR: 確認 $generator 是否涵蓋這些尺寸。"
    exit 1
}

puts ""
puts "GENIP      : 全部補產完成。接著重跑 'make check-files' 確認清單已清空。"
exit 0
