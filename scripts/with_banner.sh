#!/bin/sh
# -----------------------------------------------------------------------------
# with_banner.sh <label> <verdict-json|""> -- <command> [args...]
#
# Runs the command, then prints the large PASS/WARN/FAIL banner for it, then
# exits with the command's own status.
#
# That last part is the whole reason this lives in one file rather than being
# repeated in every Makefile recipe: if the banner ever swallowed the exit
# status, `make check` would stop failing fast and `make all` would run every
# stage regardless of what the earlier ones found -- silently, with everything
# still looking green. One copy, one test.
#
# VRA_PYTHON and VRA_BANNER come from the Makefile.
# -----------------------------------------------------------------------------

label=$1
shift
verdict=$1
shift
if [ "$1" = "--" ]; then
    shift
fi

"$@"
status=$?

python=${VRA_PYTHON:-python3}
banner=${VRA_BANNER:-}

if [ -n "$banner" ] && [ -f "$banner" ]; then
    if [ -n "$verdict" ]; then
        "$python" "$banner" --status "$status" --label "$label" \
            --verdict "$verdict" || true
    else
        "$python" "$banner" --status "$status" --label "$label" || true
    fi
fi

exit $status
