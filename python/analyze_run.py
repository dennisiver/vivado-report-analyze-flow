#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn a Vivado timing report into a compact summary an LLM can actually read.

Two modes:

  analyze   parse a ``report_timing_summary`` report, write the structured run
            record, append to the trend history, and render ``latest_<stage>.md``
  --show-path   print the full untruncated detail of one path on demand

The split is the whole point.  The agent reads only ``latest_<stage>.md``
(a page or two); the tens of thousands of lines of raw report stay on disk and
are pulled in one path at a time, and only when a question actually needs them.

Standard library only, Python 3.4+.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import manifest as manifest_mod          # noqa: E402
import trend as trend_mod                # noqa: E402
import vivado_report_parser as parser_mod  # noqa: E402


NAME_WIDTH = 58


def _shorten(name, width=NAME_WIDTH):
    """Trim a long hierarchical net name, keeping the distinctive tail."""
    if not name:
        return "-"
    if len(name) <= width:
        return name
    return "..." + name[-(width - 3):]


def _fmt(value, digits=3, suffix=""):
    if value is None:
        return "-"
    if isinstance(value, float):
        return "{0:.{1}f}{2}".format(value, digits, suffix)
    return "{0}{1}".format(value, suffix)


def _fmt_delta(change, verdict, digits=3):
    """Render a metric delta with its direction, e.g. ``-0.310 (worse)``."""
    if change is None:
        return "-"
    if verdict == "same":
        return "no change"
    return "{0:+.{1}f} ({2})".format(change, digits, verdict)


def _run_json_path(outdir, stage, timestamp):
    stamp = timestamp.replace(":", "").replace("-", "").replace("T", "_")
    return os.path.join(outdir, "history",
                        "run_{0}_{1}.json".format(stamp, stage))


def _write_text(path, text):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _write_json(path, data):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

def _render_inputs(lines, manifest, comparison, preflight):
    lines.append("## Input versions")
    if not manifest:
        lines.append("")
        lines.append("_No manifest recorded — this report was analysed "
                     "outside the pre-flight flow, so the RTL/XDC versions "
                     "behind it are unknown._")
        lines.append("")
        return

    git = manifest.get("git") or {}
    if git.get("available"):
        dirty_inputs = git.get("dirty_inputs") or []
        if dirty_inputs:
            state = "**{0} design file(s) uncommitted — this build is not " \
                    "reproducible from git**".format(len(dirty_inputs))
        elif git.get("dirty"):
            state = "design files all committed"
        else:
            state = "clean"
        lines.append("- RTL git: `{0}` on `{1}` — {2}".format(
            git.get("short"), git.get("branch"), state))
        for path in dirty_inputs[:8]:
            lines.append("  - uncommitted: `{0}`".format(path))
    else:
        lines.append("- RTL git: not a git repository (no commit provenance)")

    lines.append("- RTL sources: {0} file(s), digest `{1}`".format(
        manifest["counts"]["sources"], manifest["digest"]["sources"][:12]))
    lines.append("- XDC constraints: {0} file(s), digest `{1}`".format(
        manifest["counts"]["constraints"], manifest["digest"]["constraints"][:12]))

    if comparison:
        if comparison.get("first_run"):
            lines.append("- Change vs previous run: first tracked run")
        elif comparison.get("changed"):
            lines.append("- Change vs previous run: **inputs changed**")
            for reason in comparison.get("reasons", []):
                lines.append("  - {0}".format(reason))
        else:
            lines.append("- Change vs previous run: identical inputs "
                         "(any timing delta below is tool noise, not your edit)")

    if preflight:
        status = preflight.get("status", "unknown")
        lines.append("- Pre-flight fileset check: **{0}**".format(status))
        for warning in preflight.get("warnings", [])[:10]:
            lines.append("  - warning: {0}".format(warning))
    lines.append("")


def _render_summary(lines, summary, constraints_met, previous):
    lines.append("## Design timing summary")
    lines.append("")
    lines.append("| Metric | Value | vs previous run |")
    lines.append("|---|---|---|")

    rows = [
        ("WNS (setup worst slack)", "wns", "ns", 3),
        ("TNS (setup total neg slack)", "tns", "ns", 3),
        ("Setup failing endpoints", "tns_failing_endpoints", "", 0),
        ("WHS (hold worst slack)", "whs", "ns", 3),
        ("THS (hold total neg slack)", "ths", "ns", 3),
        ("Hold failing endpoints", "ths_failing_endpoints", "", 0),
        ("WPWS (pulse width worst slack)", "wpws", "ns", 3),
        ("Pulse width failing endpoints", "tpws_failing_endpoints", "", 0),
    ]
    for label, key, unit, digits in rows:
        value = summary.get(key)
        if value is None:
            continue
        change, verdict = trend_mod.delta(summary, previous, key)
        if key.endswith("_failing_endpoints"):
            total = summary.get(key.replace("failing", "total"))
            shown = "{0}{1}".format(value, " / {0}".format(total) if total else "")
        else:
            shown = _fmt(value, digits, unit)
        lines.append("| {0} | {1} | {2} |".format(
            label, shown, _fmt_delta(change, verdict, digits or 0)))
    lines.append("")

    if constraints_met is True:
        lines.append("**All timing constraints are met.**")
    elif constraints_met is False:
        lines.append("**Timing constraints are NOT met.**")
    lines.append("")


def _render_check_timing(lines, check_timing):
    """Surface check_timing counts — the fastest tell for missing constraints."""
    items = (check_timing or {}).get("items") or {}
    if not items:
        return

    nonzero = sorted(((name, count) for name, count in items.items() if count),
                     key=lambda pair: -pair[1])
    lines.append("## Constraint sanity (check_timing)")
    lines.append("")
    if not nonzero:
        lines.append("All {0} check_timing categories are clean.".format(len(items)))
        lines.append("")
        return

    for name, count in nonzero:
        lines.append("- `{0}`: **{1}**".format(name, count))
    lines.append("")
    if items.get("unconstrained_internal_endpoints") or items.get("no_clock"):
        lines.append("> A large `unconstrained_internal_endpoints` or any "
                     "`no_clock` count usually means a constraint file was not "
                     "applied — check the XDC list above before trusting the "
                     "slack numbers.")
        lines.append("")


def _render_clocks(lines, clocks):
    if not clocks:
        return
    lines.append("## Clocks")
    lines.append("")
    lines.append("| Clock | Period (ns) | Freq (MHz) |")
    lines.append("|---|---|---|")
    for clock in clocks:
        lines.append("| `{0}` | {1} | {2} |".format(
            clock["name"], _fmt(clock["period_ns"]), _fmt(clock["frequency_mhz"], 3)))
    lines.append("")


def _render_paths(lines, paths, limit, kind_label):
    if not paths:
        return
    lines.append("## Top {0} worst {1} paths".format(len(paths), kind_label))
    lines.append("")
    lines.append("| # | Slack (ns) | Group | Source | Destination | Levels | Logic/Route |")
    lines.append("|---|---|---|---|---|---|---|")
    for index, path in enumerate(paths, 1):
        if path.get("logic_pct") is not None and path.get("route_pct") is not None:
            split = "{0:.0f}% / {1:.0f}%".format(path["logic_pct"], path["route_pct"])
        else:
            split = "-"
        lines.append("| {0} | {1} | `{2}` | `{3}` | `{4}` | {5} | {6} |".format(
            index,
            _fmt(path.get("slack_ns")),
            path.get("path_group") or "-",
            _shorten(path.get("source")),
            _shorten(path.get("destination")),
            path.get("logic_levels") if path.get("logic_levels") is not None else "-",
            split,
        ))
    lines.append("")


def _render_endpoint_diff(lines, diff):
    if not diff or (not diff.get("new") and not diff.get("resolved")):
        return
    lines.append("## Violating endpoints vs previous run")
    lines.append("")
    for label, key in (("Newly violating", "new"), ("Resolved", "resolved")):
        entries = diff.get(key) or []
        if entries:
            lines.append("- {0} ({1}):".format(label, len(entries)))
            for name in entries[:10]:
                lines.append("  - `{0}`".format(name))
    lines.append("")


def _render_trend(lines, records, stage):
    if len(records) < 2:
        return
    lines.append("## Trend (last {0} runs, stage `{1}`)".format(
        min(len(records), 8), stage))
    lines.append("")
    lines.append("| Run | RTL | XDC | WNS (ns) | TNS (ns) | Setup fail |")
    lines.append("|---|---|---|---|---|---|")
    for record in records[-8:]:
        rtl = record.get("rtl_git_commit") or "-"
        if record.get("rtl_git_dirty"):
            rtl += "*"
        lines.append("| {0} | `{1}` | `{2}` | {3} | {4} | {5} |".format(
            record.get("timestamp", "-"),
            rtl,
            record.get("xdc_digest") or "-",
            _fmt(record.get("wns")),
            _fmt(record.get("tns")),
            record.get("tns_failing_endpoints")
            if record.get("tns_failing_endpoints") is not None else "-",
        ))
    lines.append("")
    lines.append("_`*` marks a run built from uncommitted RTL._")
    lines.append("")


def render_markdown(parsed, stage, timestamp, manifest, comparison, preflight,
                    previous, history, endpoint_diff, top_paths, run_json,
                    report_path, repo_root):
    lines = []
    meta = parsed.get("meta") or {}
    lines.append("# Timing analysis — `{0}` — {1}".format(stage, timestamp))
    lines.append("")
    lines.append("Design `{0}` on `{1}`, state `{2}`, {3}.".format(
        meta.get("design", "?"), meta.get("device", "?"),
        meta.get("design_state", "?"), meta.get("tool_version", "?")))
    lines.append("")

    _render_inputs(lines, manifest, comparison, preflight)
    _render_summary(lines, parsed.get("summary") or {},
                    parsed.get("constraints_met"), previous)
    _render_check_timing(lines, parsed.get("check_timing"))
    _render_clocks(lines, parsed.get("clocks"))
    _render_paths(lines, top_paths, len(top_paths), "setup")
    _render_endpoint_diff(lines, endpoint_diff)
    _render_trend(lines, history, stage)

    lines.append("---")
    lines.append("")
    lines.append("Full detail for a single path (do not read the raw report "
                 "directly — it is {0} lines):".format(parsed.get("total_lines", "?")))
    lines.append("")
    lines.append("```")
    lines.append("python3 {0} --show-path <destination> --run-json {1}".format(
        os.path.join(repo_root, "python", "analyze_run.py"), run_json))
    lines.append("```")
    lines.append("")
    lines.append("Raw report: `{0}`".format(report_path))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_analyze(args):
    if not os.path.isfile(args.timing_summary):
        sys.stderr.write("error: report not found: {0}\n".format(args.timing_summary))
        return 2

    parsed = parser_mod.parse_timing_summary_file(args.timing_summary)
    timestamp = datetime.datetime.now().replace(microsecond=0).isoformat()

    compare_data = manifest_mod.load_json(
        args.manifest_compare
        or manifest_mod.compare_path(args.outdir, args.stage)) or {}
    manifest = compare_data.get("manifest")
    comparison = compare_data.get("comparison")
    preflight = manifest_mod.load_json(args.preflight) if args.preflight else None

    history = trend_mod.load_history(args.outdir, stage=args.stage)
    previous = trend_mod.previous_record(history)

    summary = parsed.get("summary") or {}
    top_paths = parser_mod.worst_paths(parsed.get("paths") or [],
                                       limit=args.top, delay_kind="max")
    diff = trend_mod.endpoint_diff(
        parsed.get("paths") or [],
        previous.get("violating_endpoints") if previous else None)

    record = trend_mod.make_record(
        args.stage, timestamp, summary, manifest,
        extra={"violating_endpoints": diff["current"][:50],
               "constraints_met": parsed.get("constraints_met")})

    run_json = _run_json_path(args.outdir, args.stage, timestamp)
    _write_json(run_json, {
        "stage": args.stage,
        "timestamp": timestamp,
        "report": os.path.abspath(args.timing_summary),
        "parsed": parsed,
        "manifest": manifest,
        "manifest_comparison": comparison,
        "preflight": preflight,
    })

    trend_mod.append_history(args.outdir, record)
    history = history + [record]

    markdown = render_markdown(
        parsed, args.stage, timestamp, manifest, comparison, preflight,
        previous, history, diff, top_paths, run_json,
        os.path.abspath(args.timing_summary), args.repo_root)

    latest = os.path.join(args.outdir, "latest_{0}.md".format(args.stage))
    _write_text(latest, markdown)
    _write_text(os.path.join(args.outdir, "latest_{0}.json".format(args.stage)),
                json.dumps({"run_json": run_json, "record": record},
                           indent=2, sort_keys=True) + "\n")

    print("Timing summary written: {0}".format(latest))
    print("Run record: {0}".format(run_json))
    if summary.get("wns") is not None:
        print("WNS {0} ns, TNS {1} ns, {2} failing setup endpoint(s)".format(
            _fmt(summary.get("wns")), _fmt(summary.get("tns")),
            summary.get("tns_failing_endpoints")))
    return 0


def cmd_show_path(args):
    """Print full detail for one path, resolved by endpoint substring."""
    run_data = manifest_mod.load_json(args.run_json)
    if not run_data:
        sys.stderr.write("error: cannot read run json: {0}\n".format(args.run_json))
        return 2

    report = run_data.get("report")
    paths = (run_data.get("parsed") or {}).get("paths") or []
    needle = args.show_path.lower()

    matches = [p for p in paths
               if needle in (p.get("destination") or "").lower()
               or needle in (p.get("source") or "").lower()]
    if not matches:
        sys.stderr.write("no path matching '{0}'. Available endpoints:\n".format(
            args.show_path))
        for path in paths:
            sys.stderr.write("  {0}\n".format(path.get("destination")))
        return 1

    if not report or not os.path.isfile(report):
        sys.stderr.write("error: raw report no longer available: {0}\n".format(report))
        return 2

    for path in matches[:args.top]:
        print(parser_mod.extract_path_block(
            report, path["line_start"], path["line_end"]))
        print("")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Summarise a Vivado timing report for a context-limited agent.")
    parser.add_argument("--stage", default="impl_1",
                        help="run name this report belongs to (default: impl_1)")
    parser.add_argument("--timing-summary",
                        help="path to the report_timing_summary output")
    parser.add_argument("--outdir", default="timing_analysis",
                        help="analysis output directory (default: timing_analysis)")
    parser.add_argument("--top", type=int, default=10,
                        help="how many worst paths to keep (default: 10)")
    parser.add_argument("--manifest-compare",
                        help="manifest compare json from the pre-flight step")
    parser.add_argument("--preflight",
                        help="preflight result json from the pre-flight step")
    parser.add_argument("--repo-root",
                        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        help="root of this flow repo, used in generated hints")
    parser.add_argument("--show-path",
                        help="print full detail for the path matching this name")
    parser.add_argument("--run-json",
                        help="run record to drill into (with --show-path)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.show_path:
        if not args.run_json:
            args.run_json = os.path.join(
                args.outdir, "latest_{0}.json".format(args.stage))
            pointer = manifest_mod.load_json(args.run_json)
            if pointer and pointer.get("run_json"):
                args.run_json = pointer["run_json"]
        return cmd_show_path(args)

    if not args.timing_summary:
        sys.stderr.write("error: --timing-summary is required\n")
        return 2
    return cmd_analyze(args)


if __name__ == "__main__":
    sys.exit(main())
