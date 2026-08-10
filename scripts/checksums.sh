#!/bin/sh
# -----------------------------------------------------------------------------
# checksums.sh -- list every file in this flow with its sha256.
#
# For workstations with no git. Run it on both copies and diff the output to
# see exactly which files differ, without needing a version control system on
# the offline side:
#
#   sh scripts/checksums.sh > /tmp/workstation.txt      # on the workstation
#   sh scripts/checksums.sh > /tmp/new.txt              # on the new copy
#   diff /tmp/workstation.txt /tmp/new.txt
#
# Excludes anything generated or machine-specific: config.mk is yours, and
# timing_analysis/ is output.
# -----------------------------------------------------------------------------

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root" || exit 1

if command -v sha256sum >/dev/null 2>&1; then
    hash_cmd="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
    hash_cmd="shasum -a 256"
else
    echo "error: neither sha256sum nor shasum found" >&2
    exit 2
fi

find . -type f \
    -not -path './.git/*' \
    -not -path './timing_analysis/*' \
    -not -path '*/__pycache__/*' \
    -not -name '*.pyc' \
    -not -name 'config.mk' \
    -not -name '.DS_Store' \
    | sed 's|^\./||' \
    | LC_ALL=C sort \
    | while IFS= read -r path; do
        sum=$($hash_cmd "$path" | cut -c1-12)
        printf '%s  %s\n' "$sum" "$path"
    done
