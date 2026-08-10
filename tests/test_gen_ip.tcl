# -----------------------------------------------------------------------------
# test_gen_ip.tcl -- exercise tcl/generate_missing_ip.tcl against the fake
# Vivado, with a stand-in for the project's own IP generator script.
#
#   tclsh tests/test_gen_ip.tcl <scenario> <workdir>
#
# Scenarios:
#   ok      the generator creates the IP -> wrapper succeeds
#   nogen   the generator runs but produces nothing -> wrapper must NOT report
#           success just because the script exited cleanly
#   broken  the generator raises -> wrapper reports which script failed
#   empty   no specs to generate -> nothing to do, project never opened
# -----------------------------------------------------------------------------

set here [file normalize [file dirname [info script]]]
set repo [file dirname $here]

set scenario [lindex $argv 0]
set workdir  [file normalize [lindex $argv 1]]

file delete -force $workdir
file mkdir $workdir

source [file join $here vivado_stub.tcl]

proc write_file {path content} {
    file mkdir [file dirname $path]
    set handle [open $path w]
    puts $handle $content
    close $handle
    return $path
}

set xpr [write_file [file join $workdir build top.xpr] "fake project"]

set specs_file [file join $workdir timing_analysis manifests \
                    missing_ip_specs.txt]
if {$scenario eq "empty"} {
    write_file $specs_file ""
} else {
    write_file $specs_file "1216x80"
}

# A stand-in for gen_sram_checkdouble2.tcl, keeping the two things that matter:
# the `info exists` guard around the spec list, and deriving the module name
# from <depth>x<width>.
set body {
if {![info exists sram_specs]} {
    set sram_specs {
        2048x8
    }
}

foreach spec $sram_specs {
    regexp {(\d+)x(\d+)} $spec match depth width
    set ip_name "blk_mem_gen_${depth}x${width}"
    puts "GEN: considering $ip_name"
    if {[llength [get_ips $ip_name]] == 0} {
        BODY_ACTION
    }
}
}

switch -exact -- $scenario {
    nogen {
        set body [string map {BODY_ACTION "puts \"GEN: skipping $ip_name\""} \
                      $body]
    }
    broken {
        set body [string map {BODY_ACTION "error \"generator exploded\""} $body]
    }
    default {
        set body [string map {BODY_ACTION {
            create_ip -name blk_mem_gen -vendor xilinx.com -library ip \
                -version 8.4 -module_name $ip_name
            set_property -dict [list CONFIG.Write_Width_A $width] \
                [get_ips $ip_name]
            generate_target all [get_ips $ip_name]
            create_ip_run [get_ips $ip_name]
            launch_runs [get_runs ${ip_name}_synth_1] -jobs 4
            wait_on_run [get_runs ${ip_name}_synth_1]
        }} $body]
    }
}

set generator [write_file [file join $workdir tcl fake_gen_sram.tcl] $body]

set argv [list \
    -project $xpr \
    -script $generator \
    -specs $specs_file \
    -outdir [file join $workdir timing_analysis] \
    -jobs 4]

source [file join $repo tcl generate_missing_ip.tcl]
