#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record and compare the exact RTL/XDC inputs used by a Vivado run.

Why this exists: a run can silently use stale inputs (an edited ``.xdc`` that
was never added to ``constrs_1``, an RTL edit that Vivado decided did not
warrant a re-synthesis), and today that only becomes visible after a full
implementation has burned an hour.  A content hash of every file actually in
the filesets is the ground truth, so it is recorded before each run and diffed
against the previous one.

Git is used only as supplementary provenance: the RTL lives in a repo, the XDC
files do not, so file hashes are the common denominator that works for both.

Standard library only, Python 3.4+.
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys


CHANGED_MARKER = "##MANIFEST_CHANGED##"
STATUS_MARKER = "##MANIFEST_STATUS##"

_CHUNK = 128 * 1024


def sha256_file(path):
    """Return the hex sha256 of a file, or None if it cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except (IOError, OSError):
        return None
    return digest.hexdigest()


def _run_git(repo_root, args, strip=True):
    """Run a git command, returning stdout or None on any failure.

    ``strip`` must be disabled for porcelain output: its two-column status
    prefix is significant, and a leading space would otherwise be eaten.
    """
    try:
        output = subprocess.check_output(
            ["git"] + args, cwd=repo_root, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError):
        return None
    text = output.decode("utf-8", "replace")
    return text.strip() if strip else text


def _parse_porcelain(status):
    """Extract paths from ``git status --porcelain`` output.

    Format is ``XY <path>`` with a two-character status column; renames are
    reported as ``R  old -> new`` and we want the destination path.
    """
    paths = []
    for line in (status or "").splitlines():
        if len(line) <= 3:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        paths.append(path)
    return paths


def git_info(repo_root):
    """Collect commit/branch/dirty state for the RTL repository."""
    info = {"available": False, "repo_root": repo_root}
    if not repo_root or not os.path.isdir(repo_root):
        return info

    commit = _run_git(repo_root, ["rev-parse", "HEAD"])
    if commit is None:
        return info

    dirty_files = _parse_porcelain(
        _run_git(repo_root, ["status", "--porcelain"], strip=False))

    info.update({
        "available": True,
        "commit": commit,
        "short": commit[:12],
        "branch": _run_git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "describe": _run_git(repo_root, ["describe", "--always", "--dirty", "--tags"]),
        "dirty": bool(dirty_files),
        "dirty_files": sorted(dirty_files)[:200],
        "dirty_file_count": len(dirty_files),
    })
    return info


def _mark_dirty_inputs(records, git):
    """Flag which design files are uncommitted, ignoring unrelated repo churn.

    A dirty working tree is normal; a dirty *input to this run* is what makes
    the resulting timing numbers unreproducible, so only those are surfaced.
    """
    if not git.get("available"):
        return []

    repo_root = os.path.abspath(git.get("repo_root") or ".")
    dirty_abs = set(os.path.normpath(os.path.join(repo_root, p))
                    for p in git.get("dirty_files", []))

    dirty_inputs = []
    for record in records:
        if os.path.normpath(os.path.abspath(record["path"])) in dirty_abs:
            record["git_dirty"] = True
            dirty_inputs.append(record["path"])
    return dirty_inputs


def _file_record(path):
    record = {"path": path, "sha256": sha256_file(path)}
    try:
        stat = os.stat(path)
        record["size"] = stat.st_size
        record["mtime"] = datetime.datetime.fromtimestamp(
            stat.st_mtime).replace(microsecond=0).isoformat()
    except OSError:
        record["size"] = None
        record["mtime"] = None
        record["missing"] = True
    return record


def _aggregate_digest(records):
    """Order-independent digest over a group of files (path + content)."""
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda r: r["path"]):
        digest.update(record["path"].encode("utf-8", "replace"))
        digest.update(b"\0")
        digest.update((record["sha256"] or "MISSING").encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_manifest(stage, sources, constraints, repo_root=None):
    """Build the manifest describing one run's inputs."""
    source_records = [_file_record(p) for p in sources]
    constraint_records = [_file_record(p) for p in constraints]
    git = git_info(repo_root)
    git["dirty_inputs"] = _mark_dirty_inputs(
        source_records + constraint_records, git)
    return {
        "stage": stage,
        "timestamp": datetime.datetime.now().replace(microsecond=0).isoformat(),
        "git": git,
        "files": {
            "sources": source_records,
            "constraints": constraint_records,
        },
        "counts": {
            "sources": len(source_records),
            "constraints": len(constraint_records),
        },
        "digest": {
            "sources": _aggregate_digest(source_records),
            "constraints": _aggregate_digest(constraint_records),
        },
    }


def _diff_group(previous, current):
    """Compare two lists of file records by path and content hash."""
    prev_map = dict((r["path"], r) for r in previous)
    cur_map = dict((r["path"], r) for r in current)
    added = sorted(set(cur_map) - set(prev_map))
    removed = sorted(set(prev_map) - set(cur_map))
    modified = sorted(
        path for path in (set(cur_map) & set(prev_map))
        if cur_map[path]["sha256"] != prev_map[path]["sha256"])
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "changed": bool(added or removed or modified),
    }


def compare_manifests(previous, current):
    """Diff two manifests; ``previous`` may be None for a first-ever run."""
    if not previous:
        return {
            "first_run": True,
            "changed": True,
            "sources": {"added": [], "removed": [], "modified": [], "changed": False},
            "constraints": {"added": [], "removed": [], "modified": [], "changed": False},
            "git_commit_changed": False,
            "reasons": ["no previous manifest for this stage (first tracked run)"],
        }

    sources = _diff_group(previous["files"]["sources"], current["files"]["sources"])
    constraints = _diff_group(
        previous["files"]["constraints"], current["files"]["constraints"])

    prev_commit = (previous.get("git") or {}).get("commit")
    cur_commit = (current.get("git") or {}).get("commit")
    commit_changed = bool(prev_commit and cur_commit and prev_commit != cur_commit)

    reasons = []
    for label, diff in (("RTL source", sources), ("constraint (XDC)", constraints)):
        for kind in ("added", "removed", "modified"):
            if diff[kind]:
                reasons.append("{0} files {1}: {2}".format(
                    label, kind, ", ".join(os.path.basename(p) for p in diff[kind][:8])))
    if commit_changed:
        reasons.append("RTL git commit changed {0} -> {1}".format(
            prev_commit[:12], cur_commit[:12]))

    return {
        "first_run": False,
        "changed": bool(sources["changed"] or constraints["changed"] or commit_changed),
        "sources": sources,
        "constraints": constraints,
        "git_commit_changed": commit_changed,
        "previous_timestamp": previous.get("timestamp"),
        "reasons": reasons,
    }


def manifest_dir(outdir):
    return os.path.join(outdir, "manifests")


def _manifest_path(outdir, stage, which):
    return os.path.join(manifest_dir(outdir),
                        "manifest_{0}_{1}.json".format(stage, which))


def compare_path(outdir, stage):
    return os.path.join(manifest_dir(outdir), "compare_{0}.json".format(stage))


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
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _read_file_list(path):
    """Read a newline-separated file list as written by the Tcl pre-flight."""
    if not path:
        return []
    entries = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(os.path.normpath(line))
    # Vivado can list the same file twice across filesets; keep it stable.
    return sorted(set(entries))


def format_report(manifest, comparison):
    """Human-readable summary printed to the Vivado log during pre-flight."""
    lines = []
    git = manifest.get("git") or {}
    if git.get("available"):
        dirty_inputs = git.get("dirty_inputs") or []
        if dirty_inputs:
            state = "{0} DESIGN FILE(S) UNCOMMITTED".format(len(dirty_inputs))
        elif git.get("dirty"):
            state = "design files committed ({0} unrelated file(s) dirty)".format(
                git.get("dirty_file_count"))
        else:
            state = "clean"
        lines.append("  RTL git : {0} [{1}] {2}".format(
            git.get("short"), git.get("branch"), state))
        for path in dirty_inputs[:10]:
            lines.append("            uncommitted input: {0}".format(path))
    else:
        lines.append("  RTL git : not a git repository (no commit provenance)")

    lines.append("  Sources : {0} file(s), digest {1}".format(
        manifest["counts"]["sources"], manifest["digest"]["sources"][:12]))
    lines.append("  XDC     : {0} file(s), digest {1}".format(
        manifest["counts"]["constraints"], manifest["digest"]["constraints"][:12]))

    if comparison.get("first_run"):
        lines.append("  Change  : first tracked run for this stage")
    elif comparison["changed"]:
        lines.append("  Change  : INPUTS CHANGED since previous run")
        for reason in comparison["reasons"]:
            lines.append("            - {0}".format(reason))
    else:
        lines.append("  Change  : inputs identical to previous run ({0})".format(
            comparison.get("previous_timestamp")))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build and compare the RTL/XDC manifest for a Vivado run.")
    parser.add_argument("--stage", required=True,
                        help="run name, e.g. impl_1 or synth_1")
    parser.add_argument("--outdir", required=True,
                        help="analysis output directory (manifests/ lives here)")
    parser.add_argument("--sources-list",
                        help="file containing one RTL source path per line")
    parser.add_argument("--constraints-list",
                        help="file containing one XDC path per line")
    parser.add_argument("--repo-root",
                        help="git working tree holding the RTL")
    parser.add_argument("--save", action="store_true",
                        help="persist this manifest as the new baseline; omit "
                             "to compare only. The baseline must represent the "
                             "last successfully built run, so the caller saves "
                             "only after the build actually succeeds.")
    args = parser.parse_args(argv)

    manifest = build_manifest(
        args.stage,
        _read_file_list(args.sources_list),
        _read_file_list(args.constraints_list),
        repo_root=args.repo_root,
    )

    previous = load_json(_manifest_path(args.outdir, args.stage, "current"))
    comparison = compare_manifests(previous, manifest)

    print(format_report(manifest, comparison))

    if args.save:
        if previous:
            _write_json(_manifest_path(args.outdir, args.stage, "previous"), previous)
        _write_json(_manifest_path(args.outdir, args.stage, "current"), manifest)
        _write_json(compare_path(args.outdir, args.stage),
                    {"manifest": manifest, "comparison": comparison})

    print("{0} {1}".format(CHANGED_MARKER, "yes" if comparison["changed"] else "no"))
    print("{0} ok".format(STATUS_MARKER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
