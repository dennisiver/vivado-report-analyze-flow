#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static checks on the design file list -- no Vivado, no licence, seconds.

Two sources of truth usually coexist: an external file list (``.f``/``.tcl``)
and the ``.xpr`` filesets. They drift apart, and the drift is invisible until a
build quietly compiles the wrong set of files. This module parses the list,
checks every path it references actually exists, and reconciles it against the
project in both directions.

Path-level agreement is necessary but not sufficient: both lists can match
perfectly and a module can still have no definition anywhere, which surfaces
only as an elaboration failure much later. So the module names declared across
the file set are matched against the ones actually instantiated, and the top
module is confirmed to exist.

The ``.xpr`` is read directly as XML rather than through Vivado, which keeps
this whole stage free of the tool and finishing in seconds -- the property that
makes it worth running after every edit.

Standard library only, Python 3.4+.
"""

import argparse
import fnmatch
import json
import os
import re
import sys
import xml.etree.ElementTree as ElementTree


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

# Verilog/SystemVerilog instantiation: `name #(...) inst (` or `name inst (`.
# Anchored at line start so an expression mid-statement cannot match. The two
# identifiers must be separated by a parameter block or real whitespace --
# without that, backtracking happily splits `if (` into `i` + `f`.
_VERILOG_INST = re.compile(
    r"^[ \t]*([A-Za-z_]\w*)(?:[ \t]*#[ \t]*\([^;]*?\)[ \t]*|[ \t]+)"
    r"([A-Za-z_]\w*)[ \t]*(?:\[[^\]]*\][ \t]*)?\(",
    re.MULTILINE | re.DOTALL)

# VHDL: `entity work.foo` / `component foo`.
_VHDL_INST = re.compile(
    r"\b(?:entity\s+(?:\w+\.)?|component\s+)([A-Za-z_]\w*)", re.IGNORECASE)

_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_VHDL_COMMENT = re.compile(r"--[^\n]*")

DESIGN_EXTENSIONS = (".v", ".sv", ".vhd", ".vhdl", ".vh", ".svh")
VERILOG_EXTENSIONS = (".v", ".sv", ".vh", ".svh")

# Language keywords that the instantiation pattern would otherwise mistake for
# a module name, since `if (`, `always_ff @(`, `case (` all look alike to it.
_NOT_MODULES = frozenset("""
    if else for while repeat forever case casex casez begin end fork join
    always always_comb always_ff always_latch initial assign generate endgenerate
    function task return posedge negedge wait disable
    module endmodule input output inout wire reg logic parameter localparam
    integer real time genvar signed unsigned automatic static
    unique unique0 priority do foreach randcase assert assume cover expect
    property sequence typedef struct union enum package endpackage import export
    interface endinterface modport class endclass extern virtual pure context
    with inside dist solve before constraint rand randc
    entity architecture component process port map signal variable constant
    type subtype is of begin_ end_ when others null report severity
    posedge_ negedge_ and or not xor nand nor xnor buf bufif0 bufif1
""".split())

# Xilinx library primitives. Instantiating one is normal and its definition
# lives in UNISIM, not in the design, so it must not read as a missing module.
XILINX_PRIMITIVES = frozenset("""
    BUFG BUFGCE BUFGCE_1 BUFGCTRL BUFGMUX BUFGMUX_1 BUFGMUX_CTRL BUFH BUFHCE
    BUFIO BUFIO2 BUFMR BUFMRCE BUFR BUFG_GT BUFG_GT_SYNC BUFGCE_DIV
    IBUF IBUFDS IBUFDS_GTE2 IBUFDS_GTE3 IBUFDS_GTE4 IBUFG IBUFGDS IBUFDS_DIFF_OUT
    OBUF OBUFDS OBUFT OBUFTDS IOBUF IOBUFDS IOBUFDS_DIFF_OUT
    FDRE FDSE FDCE FDPE FDRSE LDCE LDPE
    LUT1 LUT2 LUT3 LUT4 LUT5 LUT6 LUT6_2 MUXF7 MUXF8 MUXF9 CARRY4 CARRY8
    SRL16E SRLC32E
    RAM32X1D RAM64X1D RAM128X1D RAM32M RAM64M
    RAMB18E1 RAMB36E1 RAMB18E2 RAMB36E2 FIFO18E1 FIFO36E1 FIFO18E2 FIFO36E2
    DSP48E1 DSP48E2 DSP58 DSPCPLX
    MMCME2_BASE MMCME2_ADV MMCME3_BASE MMCME3_ADV MMCME4_BASE MMCME4_ADV
    PLLE2_BASE PLLE2_ADV PLLE3_BASE PLLE3_ADV PLLE4_BASE PLLE4_ADV
    IDELAYE2 IDELAYE3 ODELAYE2 ODELAYE3 IDELAYCTRL
    ISERDESE2 ISERDESE3 OSERDESE2 OSERDESE3
    STARTUPE2 STARTUPE3 ICAPE2 ICAPE3 BSCANE2 DNA_PORT DNA_PORTE2
    XADC SYSMONE1 SYSMONE4 EFUSE_USR
    GTHE3_CHANNEL GTHE4_CHANNEL GTYE3_CHANNEL GTYE4_CHANNEL
    GTHE3_COMMON GTHE4_COMMON GTYE3_COMMON GTYE4_COMMON
    PS7 PS8 VCC GND KEEPER PULLUP PULLDOWN
""".split())


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


def is_excluded(path, patterns):
    """Glob match, mirroring ``::vra::is_excluded`` in the stage 2 Tcl audit.

    Matched against the normalised absolute path, exactly as stage 2 does with
    Tcl's ``string match``: same case sensitivity, and ``*`` crosses ``/`` in
    both. Matching anything else here -- a path relative to the cwd, say --
    would be friendlier to write but would let one pattern hit in stage 1 and
    miss in stage 2, which is the very inconsistency this option exists to fix.

    So patterns are anchored on absolute paths: write ``*/old_mem/*``, not
    ``old_mem/*``. A pattern that matches nothing is reported rather than
    passing for a clean result.
    """
    if not patterns:
        return False
    candidate = os.path.normpath(os.path.abspath(path))
    for pattern in patterns:
        if fnmatch.fnmatchcase(candidate, pattern):
            return True
    return False


def _partition_excluded(paths, patterns):
    """``(kept, excluded)`` for one list of paths."""
    kept, excluded = [], []
    for path in paths or []:
        (excluded if is_excluded(path, patterns) else kept).append(path)
    return kept, excluded


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


def parse_xpr(path):
    """Read the source files and top module straight out of a ``.xpr``.

    The project file is XML, so this needs no Vivado -- which is what lets the
    reconciliation run in the same seconds-long, tool-free stage as the
    existence checks. Vivado writes paths with ``$PPRDIR`` (the directory
    holding the .xpr) and ``$PSRCDIR`` (its .srcs directory); both are expanded
    here.

    Returns None when the file cannot be understood, so the caller can report
    "not checked" rather than a misleading empty result.
    """
    if not path or not os.path.isfile(path):
        return None

    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, IOError, OSError):
        return None

    project_dir = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    variables = {
        "$PPRDIR": project_dir,
        "$PSRCDIR": os.path.join(project_dir, "{0}.srcs".format(stem)),
    }

    def expand(raw):
        value = raw
        for name, replacement in variables.items():
            value = value.replace(name, replacement)
        return os.path.normpath(os.path.join(project_dir, value))

    filesets = {}
    for fileset in root.iter("FileSet"):
        name = fileset.get("Name") or ""
        paths = []
        for node in fileset.iter("File"):
            raw = node.get("Path")
            if raw:
                paths.append(expand(raw))
        filesets[name] = paths

    if not filesets:
        return None

    top = None
    for config in root.iter("Config"):
        for option in config.iter("Option"):
            if option.get("Name") == "TopModule":
                top = option.get("Val")
                break
        if top:
            break

    sources = []
    constraints = []
    for name, paths in filesets.items():
        lowered = name.lower()
        if "constr" in lowered:
            constraints.extend(paths)
        elif "sim" in lowered:
            continue  # simulation-only sources are not part of the build
        else:
            sources.extend(paths)

    return {
        "project": os.path.abspath(path),
        "filesets": filesets,
        "sources": sorted(set(sources)),
        "constraints": sorted(set(constraints)),
        "top": top,
        "ip": sorted(set(p for p in sources + constraints
                         if p.lower().endswith(".xci"))),
    }


def _strip_comments(text, verilog):
    if verilog:
        return _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", text))
    return _VHDL_COMMENT.sub(" ", text)


def scan_modules(paths):
    """Map declared module/entity names to files, and collect instantiations.

    A regex scan rather than a parser. That is enough to answer "is anything
    instantiated that nobody defines", which is the question that turns into an
    elaboration failure, but it means the instantiation set can over-report --
    hence the keyword and primitive filtering, and the two-tier grading in the
    caller.
    """
    declared = {}
    instantiated = {}

    for path in sorted(set(paths)):
        lowered = path.lower()
        if not lowered.endswith(DESIGN_EXTENSIONS):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except (IOError, OSError):
            continue

        verilog = lowered.endswith(VERILOG_EXTENSIONS)
        text = _strip_comments(text, verilog)

        for name in set(_MODULE_DECL.findall(text)):
            declared.setdefault(name, []).append(path)

        if verilog:
            names = set(match.group(1) for match in _VERILOG_INST.finditer(text))
        else:
            names = set(_VHDL_INST.findall(text))

        for name in names:
            if name.lower() in _NOT_MODULES:
                continue
            instantiated.setdefault(name, []).append(path)

    # A module instantiating itself by name is a declaration, not a dependency.
    for name in list(instantiated):
        if name in declared and instantiated[name] == declared[name]:
            del instantiated[name]

    return {"declared": dict((k, sorted(v)) for k, v in declared.items()),
            "instantiated": dict((k, sorted(v))
                                 for k, v in instantiated.items())}


def find_missing_modules(in_scope, on_disk, ignore=None):
    """Split undefined instantiations by how confident we are about them.

    Two outcomes deserve very different treatment:

    ``file_missing``  the module *is* defined, in a file that exists on disk
                      but is not in the build. Certain, and the message can name
                      the file to add.
    ``undefined``     nothing we scanned defines it. Could equally be an IP, a
                      vendor primitive we do not list, or an artefact of the
                      regex, so it is graded lower.
    """
    ignored = set(ignore or ())
    declared = set(in_scope["declared"])
    elsewhere = on_disk["declared"] if on_disk else {}

    file_missing = {}
    undefined = {}
    for name, users in sorted(in_scope["instantiated"].items()):
        if (name in declared or name in ignored
                or name.upper() in XILINX_PRIMITIVES):
            continue
        if name in elsewhere:
            file_missing[name] = {"defined_in": elsewhere[name],
                                  "instantiated_in": users}
        else:
            undefined[name] = {"instantiated_in": users}

    return {"file_missing": file_missing, "undefined": undefined}


# Xilinx block-memory IP instances are named <core>_<depth>x<width> by the
# project's own generator script, which is what makes the missing ones
# recoverable: the name carries the geometry needed to regenerate them.
_IP_SPEC = re.compile(r"^blk_mem_gen_(\d+)x(\d+)$")


def sram_specs(result):
    """``<depth>x<width>`` for every missing module named like a blk_mem_gen IP.

    Feeds the project's existing IP generator script. Names that do not match
    the pattern are left out on purpose -- that script cannot produce them, and
    a spec it cannot honour would be a silent no-op rather than a fix.
    """
    names = set(result.get("undefined") or {})
    names.update(result.get("ip_missing") or {})

    specs = set()
    for name in names:
        match = _IP_SPEC.match(name)
        if match:
            specs.add("{0}x{1}".format(match.group(1), match.group(2)))
    return sorted(specs)


def check(filelist_paths, fileset_files=None, scan_duplicates=True,
          project=None, search_dirs=None, ignore_modules=None,
          project_source=None, exclude=None):
    """Run every static check and return one structured result.

    ``project`` is a ``.xpr`` to reconcile against; ``fileset_files`` an
    already-extracted list (from the Tcl audit) used when the .xpr cannot be
    read. ``search_dirs`` widens the module scan beyond the build so a module
    defined in a file nobody added can still be located and named.
    ``exclude`` is a list of globs declaring paths out of scope; they drop out
    of every check here, and the result records what was dropped.
    """
    combined = {"files": [], "incdirs": [], "defines": [], "lists": [],
                "errors": []}
    for path in filelist_paths or []:
        parsed = parse_filelist(path)
        for key in combined:
            combined[key].extend(parsed[key])

    # Applied before anything looks at the files: declaring a path out of scope
    # has to take it out of the existence check, the module scan and the
    # project reconciliation alike, or the three would disagree about what the
    # build even is.
    patterns = list(exclude or ())
    combined["files"], excluded = _partition_excluded(combined["files"],
                                                      patterns)

    result = {
        "available": bool(filelist_paths),
        "lists": sorted(set(combined["lists"])),
        "parse_errors": combined["errors"],
        "files": sorted(set(combined["files"])),
        "incdirs": sorted(set(combined["incdirs"])),
        "defines": sorted(set(combined["defines"])),
        "exclude_patterns": patterns,
        "excluded": sorted(set(excluded)),
    }
    result.update(check_existence(combined))

    # --- reconcile against the project ------------------------------------
    xpr = parse_xpr(project) if project else None
    project_files = None
    if xpr:
        project_files = xpr["sources"] + xpr["constraints"]
        result["project_source"] = "xpr"
        result["project_top"] = xpr.get("top")
    elif fileset_files is not None:
        project_files = fileset_files
        result["project_source"] = project_source or "fileset-list"
    else:
        result["project_source"] = None
        result["project_reason"] = (
            "could not read {0}".format(project) if project
            else "no .xpr or fileset list supplied")

    if project_files is not None:
        # The same exclusions apply to the project side. Otherwise a path
        # declared out of scope would vanish from the file list and then
        # reappear as "only in project".
        project_files, project_excluded = _partition_excluded(project_files,
                                                              patterns)
        if project_excluded:
            result["excluded"] = sorted(set(result["excluded"])
                                        | set(project_excluded))
        result["reconcile"] = reconcile(combined["files"], project_files)

    # --- module level -----------------------------------------------------
    # Everything in the build, plus whatever the project knows about, is what
    # elaboration would actually see.
    in_build = set(os.path.abspath(p) for p in combined["files"]
                   if os.path.isfile(p))
    if project_files:
        in_build.update(os.path.abspath(p) for p in project_files
                        if os.path.isfile(p))

    in_scope = scan_modules(in_build)
    result["modules_declared"] = sorted(in_scope["declared"])
    result["modules_instantiated"] = sorted(in_scope["instantiated"])

    # Widen the search so a module defined in an unreferenced file can be
    # pinpointed rather than merely reported as missing.
    # Paths are normalised throughout: the same file reached via a relative
    # search dir and via an absolute project path must not look like two files.
    candidates = set()
    for directory in search_dirs or []:
        for root, _, names in os.walk(directory):
            for name in names:
                if name.lower().endswith(DESIGN_EXTENSIONS):
                    candidates.add(os.path.abspath(os.path.join(root, name)))
    for path in in_build:
        directory = os.path.dirname(path)
        if os.path.isdir(directory):
            for name in os.listdir(directory):
                full = os.path.abspath(os.path.join(directory, name))
                if (os.path.isfile(full)
                        and name.lower().endswith(DESIGN_EXTENSIONS)):
                    candidates.add(full)

    outside = candidates - in_build
    on_disk = scan_modules(outside) if outside else None

    ignore = set(ignore_modules or ())
    if xpr:
        # An IP's RTL is generated, not in the source tree; its module name is
        # the .xci basename.
        ignore.update(os.path.splitext(os.path.basename(p))[0]
                      for p in xpr.get("ip") or [])
    result["ignored_modules"] = sorted(ignore)
    result.update(find_missing_modules(in_scope, on_disk, ignore))

    top = result.get("project_top")
    if top:
        result["top_found"] = top in in_scope["declared"]

    if scan_duplicates:
        result["duplicate_modules"] = find_duplicate_modules(in_build)

    return result


def format_report(result):
    lines = []
    if not result.get("available"):
        return "  no file list supplied"

    lines.append("  lists   : {0}".format(len(result["lists"])))
    lines.append("  files   : {0} referenced, {1} missing".format(
        result["file_count"], len(result["missing_files"])))

    # Always printed when patterns were given, including when nothing matched.
    # A silent exclusion is indistinguishable from a clean result, and a
    # mistyped pattern that quietly excludes nothing is worse still.
    patterns = result.get("exclude_patterns") or []
    if patterns:
        excluded = result.get("excluded") or []
        lines.append("  excluded: 依 {0} 個樣式排除了 {1} 個檔案（未經檢查）"
                     .format(len(patterns), len(excluded)))
        for pattern in patterns:
            hits = sum(1 for path in excluded if is_excluded(path, [pattern]))
            lines.append("            {0}  -> {1} 個{2}".format(
                pattern, hits, "  ← 沒有命中任何檔案" if not hits else ""))
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
        source = result.get("project_source") or "project"
        if reconciliation["consistent"]:
            lines.append("  project : file list and {0} agree".format(source))
        else:
            lines.append("  project : file list and {0} DISAGREE "
                         "({1} only in list, {2} only in project)".format(
                             source,
                             len(reconciliation["only_in_filelist"]),
                             len(reconciliation["only_in_project"])))
            for path in reconciliation["only_in_filelist"][:10]:
                lines.append("            only in file list: {0}".format(path))
            for path in reconciliation["only_in_project"][:10]:
                lines.append("            only in project  : {0}".format(path))
    else:
        lines.append("  project : NOT CHECKED ({0})".format(
            result.get("project_reason", "no project supplied")))

    lines.append("  modules : {0} declared, {1} instantiated".format(
        len(result.get("modules_declared") or []),
        len(result.get("modules_instantiated") or [])))

    for name, detail in sorted((result.get("file_missing") or {}).items())[:10]:
        lines.append("            MISSING FILE for module '{0}' -- "
                     "defined in {1}".format(
                         name, ", ".join(detail["defined_in"][:2])))
    for name, detail in sorted((result.get("undefined") or {}).items())[:10]:
        lines.append("            UNDEFINED module '{0}' -- used in {1}".format(
            name, ", ".join(os.path.basename(p)
                            for p in detail["instantiated_in"][:2])))

    specs = sram_specs(result)
    if specs:
        lines.append("  ip      : {0} 個缺少的 IP 可由產生腳本補上：{1}".format(
            len(specs), "、".join(specs)))
        lines.append("            執行 'make gen-ip'（需先在 config.mk 設定 "
                     "SRAM_GEN_TCL）")

    if result.get("top_found") is False:
        lines.append("  top     : '{0}' NOT FOUND in any source file".format(
            result.get("project_top")))
    elif result.get("top_found"):
        lines.append("  top     : {0}".format(result.get("project_top")))

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
    parser.add_argument("--project",
                        help="Vivado .xpr to reconcile against; read as XML so "
                             "no Vivado is needed")
    parser.add_argument("--fileset-list",
                        help="file holding the .xpr fileset paths, one per "
                             "line, as written by the Tcl project audit. Used "
                             "when the .xpr itself cannot be read")
    parser.add_argument("--search-dir", action="append", default=[],
                        help="also scan this directory for module definitions, "
                             "so a module in an unreferenced file can be named "
                             "(repeatable)")
    parser.add_argument("--ignore-module", action="append", default=[],
                        help="treat this module as defined elsewhere "
                             "(repeatable)")
    parser.add_argument("--exclude", action="append", default=[],
                        help="glob for paths that are out of scope, e.g. "
                             "'*/old_mem/*'. Same patterns the Tcl audit takes "
                             "(repeatable). Excluded files are reported, never "
                             "silently dropped")
    parser.add_argument("--outdir", default="timing_analysis")
    parser.add_argument("--no-duplicate-scan", action="store_true")
    args = parser.parse_args(argv)

    if not args.filelist:
        sys.stderr.write("error: at least one --filelist is required\n")
        return 2

    fileset = _read_lines(args.fileset_list) if args.fileset_list else None
    result = check(args.filelist, fileset,
                   scan_duplicates=not args.no_duplicate_scan,
                   project=args.project, search_dirs=args.search_dir,
                   ignore_modules=args.ignore_module, exclude=args.exclude)

    print(format_report(result))

    directory = os.path.join(args.outdir, "manifests")
    if not os.path.isdir(directory):
        os.makedirs(directory)
    with open(os.path.join(directory, "filelist_check.json"),
              "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")

    # Always rewritten, including when empty: a stale spec file left over from
    # a previous run would have `make gen-ip` generate IP nobody is missing.
    with open(os.path.join(directory, "missing_ip_specs.txt"),
              "w", encoding="utf-8") as handle:
        for spec in sram_specs(result):
            handle.write("{0}\n".format(spec))

    # Grade this stage now, so the end-of-stage banner and the pre-check
    # summary read one assessment rather than each deriving its own.
    try:
        import risk_rules
        with open(os.path.join(args.outdir, "risk_files.json"),
                  "w", encoding="utf-8") as handle:
            json.dump(risk_rules.evaluate(filelist=result), handle,
                      indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
    except ImportError:
        pass

    broken = bool(result["missing_files"] or result["parse_errors"]
                  or result.get("file_missing")
                  or result.get("top_found") is False
                  or (result.get("reconcile")
                      and not result["reconcile"]["consistent"]))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
