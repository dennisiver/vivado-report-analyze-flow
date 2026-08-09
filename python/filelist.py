#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static checks on the design file list -- no Vivado, no licence, seconds.

Two sources of truth usually coexist: an external file list (``.f``/``.tcl``)
and the ``.xpr`` filesets. They drift apart, and the drift is invisible until a
build quietly compiles the wrong set of files. This module parses the list,
checks every path it references actually exists, and reconciles it against the
project in both directions.

Being pure Python means an agent can run this at any time, including before
Vivado is even started -- which is the point, since a missing file costs
nothing to find here and an hour to find during synthesis.

Standard library only, Python 3.4+.
"""

import argparse
import json
import os
import re
import sys


# .f syntax: one path per line, -f/-F recurse into another list, +incdir+ adds
# search paths, // and # start comments.
_RE_INCDIR = re.compile(r"^\+incdir\+(.*)$")
_RE_DEFINE = re.compile(r"^\+define\+(.*)$")
_RE_COMMENT = re.compile(r"(^|\s)(//|#).*$")

# Tcl lists: pull the file arguments out of the usual commands.
_RE_TCL_CMD = re.compile(
    r"^\s*(?:add_files|read_verilog|read_vhdl|read_xdc|read_ip|read_mem|"
    r"import_files)\b(.*)$")
_RE_TCL_OPTION = re.compile(r"^-\S+$")

# Options whose *next* argument is a value, not a file. Boolean switches such
# as -norecurse or -quiet are deliberately absent: consuming their successor
# would swallow a real filename.
_TCL_OPTIONS_WITH_VALUE = frozenset(
    ["-fileset", "-of_objects", "-copy_to", "-library", "-sub_design"])

_MODULE_DECL = re.compile(
    r"^\s*(?:module|entity)\s+([A-Za-z_]\w*)", re.MULTILINE)

DESIGN_EXTENSIONS = (".v", ".sv", ".vhd", ".vhdl", ".vh", ".svh")


def _strip_comment(line):
    return _RE_COMMENT.sub("", line).strip()


def _tcl_arguments(text):
    """Split a Tcl command tail into arguments, honouring quotes and braces."""
    arguments = []
    for token in re.findall(r'\{([^{}]*)\}|"([^"]*)"|(\S+)', text):
        value = token[0] or token[1] or token[2]
        if value:
            arguments.append(value)
    return arguments


def parse_filelist(path, _seen=None, _depth=0):
    """Parse a .f / .tcl / .txt file list into files, include dirs and defines.

    Paths are resolved relative to the list that names them, which is the
    convention simulators and Vivado both follow. Nested lists are followed
    once each so a cyclic ``-f`` cannot hang the checker.
    """
    result = {"files": [], "incdirs": [], "defines": [], "lists": [],
              "errors": []}
    if _depth > 16:
        result["errors"].append("file list nesting too deep at {0}".format(path))
        return result

    path = os.path.abspath(path)
    seen = _seen if _seen is not None else set()
    if path in seen:
        return result
    seen.add(path)
    result["lists"].append(path)

    if not os.path.isfile(path):
        result["errors"].append("file list not found: {0}".format(path))
        return result

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except (IOError, OSError) as error:
        result["errors"].append("cannot read {0}: {1}".format(path, error))
        return result

    base = os.path.dirname(path)
    is_tcl = path.lower().endswith(".tcl")

    def resolve(entry):
        entry = os.path.expandvars(os.path.expanduser(entry))
        if not os.path.isabs(entry):
            entry = os.path.join(base, entry)
        return os.path.normpath(entry)

    def recurse(entry):
        nested = parse_filelist(resolve(entry), seen, _depth + 1)
        for key in ("files", "incdirs", "defines", "lists", "errors"):
            result[key].extend(nested[key])

    if is_tcl:
        for line in text.splitlines():
            match = _RE_TCL_CMD.match(_strip_comment(line))
            if not match:
                continue
            skip_next = False
            for argument in _tcl_arguments(match.group(1)):
                if skip_next:
                    skip_next = False
                    continue
                if _RE_TCL_OPTION.match(argument):
                    skip_next = argument in _TCL_OPTIONS_WITH_VALUE
                    continue
                result["files"].append(resolve(argument))
        return result

    pending_list = False
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line:
            continue

        for token in line.split():
            if pending_list:
                recurse(token)
                pending_list = False
                continue

            if token in ("-f", "-F"):
                pending_list = True
                continue

            incdir = _RE_INCDIR.match(token)
            if incdir:
                result["incdirs"].append(resolve(incdir.group(1)))
                continue

            define = _RE_DEFINE.match(token)
            if define:
                result["defines"].append(define.group(1))
                continue

            if token.startswith(("-", "+")):
                continue  # other simulator switches are not our business

            result["files"].append(resolve(token))

    return result


def check_existence(parsed):
    """Split the parsed list into what exists and what does not."""
    missing_files = [p for p in parsed["files"] if not os.path.isfile(p)]
    missing_incdirs = [d for d in parsed["incdirs"] if not os.path.isdir(d)]
    return {
        "missing_files": sorted(set(missing_files)),
        "missing_incdirs": sorted(set(missing_incdirs)),
        "file_count": len(set(parsed["files"])),
        "incdir_count": len(set(parsed["incdirs"])),
    }


def _normalise(paths):
    return set(os.path.normpath(os.path.abspath(p)) for p in paths or [])


def reconcile(filelist_files, fileset_files):
    """Compare the file list against the project filesets, both directions."""
    in_list = _normalise(filelist_files)
    in_project = _normalise(fileset_files)
    return {
        "only_in_filelist": sorted(in_list - in_project),
        "only_in_project": sorted(in_project - in_list),
        "consistent": in_list == in_project,
    }


def find_duplicate_modules(paths):
    """Report module/entity names declared in more than one file.

    A cheap regex scan, not a parser: it is looking for the case where two
    files define the same module and which one wins depends on read order.
    """
    owners = {}
    for path in sorted(set(paths)):
        if not path.lower().endswith(DESIGN_EXTENSIONS):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except (IOError, OSError):
            continue
        for name in set(_MODULE_DECL.findall(text)):
            owners.setdefault(name, []).append(path)

    return dict((name, sorted(files))
                for name, files in owners.items() if len(files) > 1)


def check(filelist_paths, fileset_files=None, scan_duplicates=True):
    """Run every static check and return one structured result."""
    combined = {"files": [], "incdirs": [], "defines": [], "lists": [],
                "errors": []}
    for path in filelist_paths or []:
        parsed = parse_filelist(path)
        for key in combined:
            combined[key].extend(parsed[key])

    result = {
        "available": bool(filelist_paths),
        "lists": sorted(set(combined["lists"])),
        "parse_errors": combined["errors"],
        "files": sorted(set(combined["files"])),
        "incdirs": sorted(set(combined["incdirs"])),
        "defines": sorted(set(combined["defines"])),
    }
    result.update(check_existence(combined))

    if fileset_files is not None:
        result["reconcile"] = reconcile(combined["files"], fileset_files)

    if scan_duplicates:
        existing = [p for p in set(combined["files"]) if os.path.isfile(p)]
        result["duplicate_modules"] = find_duplicate_modules(existing)

    return result


def format_report(result):
    lines = []
    if not result.get("available"):
        return "  no file list supplied"

    lines.append("  lists   : {0}".format(len(result["lists"])))
    lines.append("  files   : {0} referenced, {1} missing".format(
        result["file_count"], len(result["missing_files"])))
    for path in result["missing_files"][:20]:
        lines.append("            MISSING {0}".format(path))
    if result["missing_incdirs"]:
        lines.append("  incdirs : {0} missing".format(
            len(result["missing_incdirs"])))
        for path in result["missing_incdirs"][:10]:
            lines.append("            MISSING {0}".format(path))
    for error in result["parse_errors"][:10]:
        lines.append("  parse   : {0}".format(error))

    reconciliation = result.get("reconcile")
    if reconciliation:
        if reconciliation["consistent"]:
            lines.append("  project : file list and .xpr agree")
        else:
            lines.append("  project : file list and .xpr DISAGREE "
                         "({0} only in list, {1} only in project)".format(
                             len(reconciliation["only_in_filelist"]),
                             len(reconciliation["only_in_project"])))
            for path in reconciliation["only_in_filelist"][:10]:
                lines.append("            only in file list: {0}".format(path))
            for path in reconciliation["only_in_project"][:10]:
                lines.append("            only in project  : {0}".format(path))

    duplicates = result.get("duplicate_modules") or {}
    for name, files in sorted(duplicates.items())[:10]:
        lines.append("  duplicate module '{0}' in {1} files".format(
            name, len(files)))

    return "\n".join(lines)


def _read_lines(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return [line.strip() for line in handle if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Static file list checks; needs no Vivado.")
    parser.add_argument("--filelist", action="append", default=[],
                        help="file list to check (repeatable)")
    parser.add_argument("--fileset-list",
                        help="file holding the .xpr fileset paths, one per "
                             "line, as written by the Tcl project audit")
    parser.add_argument("--outdir", default="timing_analysis")
    parser.add_argument("--no-duplicate-scan", action="store_true")
    args = parser.parse_args(argv)

    if not args.filelist:
        sys.stderr.write("error: at least one --filelist is required\n")
        return 2

    fileset = _read_lines(args.fileset_list) if args.fileset_list else None
    result = check(args.filelist, fileset,
                   scan_duplicates=not args.no_duplicate_scan)

    print(format_report(result))

    directory = os.path.join(args.outdir, "manifests")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(os.path.join(directory, "filelist_check.json"),
              "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")

    broken = bool(result["missing_files"] or result["parse_errors"]
                  or (result.get("reconcile")
                      and not result["reconcile"]["consistent"]))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
