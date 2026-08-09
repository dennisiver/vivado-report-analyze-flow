#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record the tool and OS environment, and detect when it changes between runs.

Changing Vivado version silently invalidates everything you learned from the
previous build: timing numbers stop being comparable, IP may need regenerating,
and a report layout this flow parses may have moved. That is the same
"silently using something different" failure the rest of this repo exists to
catch, so the environment gets the same treatment as RTL and XDC -- recorded on
every run, diffed against the last one.

Vivado's own version string comes from tcl/check_environment.tcl, since asking
the tool is the only reliable source. Everything else is read from the OS here.

Standard library only, Python 3.4+.
"""

import argparse
import datetime
import json
import os
import platform
import sys


CHANGED_MARKER = "##ENV_CHANGED##"

# Platforms AMD/Xilinx list as supported for Vivado 2024.2. Running elsewhere
# usually works but is worth stating plainly on a sign-off report.
SUPPORTED_OS = {
    "rhel": ["8", "9"],
    "centos": ["8", "9"],
    "rocky": ["8", "9"],
    "almalinux": ["8", "9"],
    "ubuntu": ["20.04", "22.04"],
    "sles": ["15"],
    "opensuse-leap": ["15"],
}


def _read_os_release():
    """Parse /etc/os-release into a dict; empty when unavailable."""
    values = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    values[key.strip().lower()] = value.strip().strip('"\'')
            break
        except (IOError, OSError):
            continue
    return values


def detect_os():
    release = _read_os_release()
    identifier = (release.get("id") or "").lower()
    version = release.get("version_id") or ""

    supported = None
    if identifier in SUPPORTED_OS:
        prefixes = SUPPORTED_OS[identifier]
        supported = any(version == p or version.startswith(p + ".")
                        for p in prefixes)
    elif identifier:
        supported = False

    return {
        "id": identifier or "unknown",
        "version_id": version,
        "pretty_name": release.get("pretty_name") or platform.platform(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "supported_by_vivado_2024_2": supported,
    }


def detect_python():
    return {
        "executable": sys.executable,
        "version": "{0}.{1}.{2}".format(*sys.version_info[:3]),
        "version_full": sys.version.replace("\n", " "),
    }


def detect_host():
    return {
        "hostname": platform.node(),
        "user": os.environ.get("USER") or os.environ.get("LOGNAME") or "",
    }


def detect_vivado(info_path=None, version=None):
    """Vivado details, preferably as written by tcl/check_environment.tcl."""
    result = {
        "version": version or "",
        "build": "",
        "install_root": os.environ.get("XILINX_VIVADO", ""),
        "source": "argument" if version else "unavailable",
    }

    if info_path and os.path.isfile(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            result.update({
                "version": data.get("version", result["version"]),
                "build": data.get("build", ""),
                "install_root": data.get("install_root") or result["install_root"],
                "device_count": data.get("device_count"),
                "source": "vivado",
            })
        except (IOError, OSError, ValueError) as error:
            result["parse_error"] = str(error)

    return result


def build_environment(vivado_info=None, vivado_version=None):
    return {
        "timestamp": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "vivado": detect_vivado(vivado_info, vivado_version),
        "os": detect_os(),
        "python": detect_python(),
        "host": detect_host(),
    }


def compare_environments(previous, current):
    """Diff two environment records, worst-mattering fields first."""
    if not previous:
        return {"first_run": True, "changed": True, "changes": [],
                "vivado_changed": False, "reasons":
                    ["no environment baseline yet (first tracked run)"]}

    changes = []
    reasons = []

    old_vivado = (previous.get("vivado") or {}).get("version") or ""
    new_vivado = (current.get("vivado") or {}).get("version") or ""
    vivado_changed = bool(old_vivado and new_vivado and old_vivado != new_vivado)
    if vivado_changed:
        changes.append({"field": "vivado.version",
                        "from": old_vivado, "to": new_vivado})
        reasons.append("Vivado 版本由 {0} 變成 {1}".format(old_vivado, new_vivado))

    for field, label in (("os.id", "OS"), ("os.version_id", "OS 版本"),
                         ("host.hostname", "主機"),
                         ("python.version", "Python 版本")):
        section, key = field.split(".")
        old = (previous.get(section) or {}).get(key) or ""
        new = (current.get(section) or {}).get(key) or ""
        if old and new and old != new:
            changes.append({"field": field, "from": old, "to": new})
            reasons.append("{0} 由 {1} 變成 {2}".format(label, old, new))

    return {
        "first_run": False,
        "changed": bool(changes),
        "changes": changes,
        "vivado_changed": vivado_changed,
        "previous_timestamp": previous.get("timestamp"),
        "reasons": reasons,
    }


def _path(outdir, which):
    return os.path.join(outdir, "manifests",
                        "environment_{0}.json".format(which))


def compare_path(outdir):
    return os.path.join(outdir, "manifests", "environment_compare.json")


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError):
        return None


def _write_json(path, data):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def format_report(environment, comparison):
    lines = []
    vivado = environment["vivado"]
    lines.append("  Vivado  : {0} {1}".format(
        vivado.get("version") or "<unknown>", vivado.get("build") or ""))
    operating = environment["os"]
    supported = operating.get("supported_by_vivado_2024_2")
    if supported is True:
        note = "supported"
    elif supported is False:
        note = "NOT in the Vivado 2024.2 supported list"
    else:
        note = "support status unknown"
    lines.append("  OS      : {0} (kernel {1}) -- {2}".format(
        operating.get("pretty_name"), operating.get("kernel"), note))
    lines.append("  Python  : {0} ({1})".format(
        environment["python"]["version"], environment["python"]["executable"]))
    lines.append("  Host    : {0}".format(environment["host"]["hostname"]))

    if comparison.get("first_run"):
        lines.append("  Change  : first tracked run, baseline created")
    elif comparison["changed"]:
        lines.append("  Change  : ENVIRONMENT CHANGED since the last run")
        for reason in comparison["reasons"]:
            lines.append("            - {0}".format(reason))
    else:
        lines.append("  Change  : identical to the last run ({0})".format(
            comparison.get("previous_timestamp")))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record and compare the tool/OS environment for a run.")
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--vivado-info",
                        help="json written by tcl/check_environment.tcl")
    parser.add_argument("--vivado-version",
                        help="Vivado version string, when known already")
    parser.add_argument("--save", action="store_true",
                        help="store this as the new baseline")
    args = parser.parse_args(argv)

    environment = build_environment(args.vivado_info, args.vivado_version)
    previous = load_json(_path(args.outdir, "current"))
    comparison = compare_environments(previous, environment)

    print(format_report(environment, comparison))

    if args.save:
        if previous:
            _write_json(_path(args.outdir, "previous"), previous)
        _write_json(_path(args.outdir, "current"), environment)
    _write_json(compare_path(args.outdir),
                {"environment": environment, "comparison": comparison})

    print("{0} {1}".format(CHANGED_MARKER,
                           "yes" if comparison["changed"] else "no"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
