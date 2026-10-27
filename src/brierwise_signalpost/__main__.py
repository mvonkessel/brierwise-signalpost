"""Command line: python -m brierwise_signalpost run --organisations BATCH.jsonl --output envelopes.jsonl --report report.json"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

from .run import run_batch


def keep_snapshot(output: str) -> str | None:
    """The envelopes an earlier run left at `output`, preserved under snapshots/ next to it; returns the copy's path.
    Running the same command again therefore refreshes against the previous run without extra arguments."""
    try:
        with open(output, encoding="utf-8") as fh:
            first = json.loads(fh.readline())
        run_id = str((first.get("run") or {}).get("run_id") or "unknown").replace("/", "_").replace("\\", "_")
    except (OSError, ValueError):
        return None
    folder = os.path.join(os.path.dirname(output), "snapshots")  # a relative output keeps a relative path in the report
    os.makedirs(folder, exist_ok=True)
    copy = os.path.join(folder, "envelopes-%s.jsonl" % run_id)
    if not os.path.exists(copy):
        shutil.copyfile(output, copy)
    return copy


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="brierwise_signalpost")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="research a batch of organisation numbers")
    r.add_argument("--organisations", required=True, help="JSONL with organisation_number, or one number per line")
    r.add_argument("--output", required=True, help="output JSONL: one terminal envelope per input")
    r.add_argument("--report", required=True, help="machine-readable run report (JSON)")
    r.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ"))
    r.add_argument("--workers", type=int, default=12)
    r.add_argument("--expected-count", type=int)
    r.add_argument("--site", help="also build the static profile site into this folder")
    r.add_argument("--previous", help="envelopes of the previous run (JSONL): changes and history are derived from it; "
                   "by default the envelopes an earlier run left at --output (kept under snapshots/)")
    r.add_argument("--no-previous", action="store_true", help="do not use an earlier output as the previous run")
    r.add_argument("--nav-days", type=int, default=200, help="Nav job feed window in days (0 switches it off)")
    s = sub.add_parser("site", help="build the static profile site from envelopes")
    s.add_argument("--envelopes", required=True)
    s.add_argument("--output", required=True)
    a = p.parse_args(argv)
    if a.cmd == "run":
        previous = a.previous
        if not previous and not a.no_previous and os.path.exists(a.output) and os.path.getsize(a.output) > 0:
            previous = keep_snapshot(a.output)
        rep = run_batch(a.organisations, a.output, a.report, a.run_id, a.workers, a.expected_count,
                        nav_days=a.nav_days, previous=previous)
        if a.site:
            from .site import build
            build(a.output, a.site)
        print(json.dumps({k: rep[k] for k in ("run_id", "envelopes", "terminal_status", "wall_seconds",
                                               "requests_total")}, ensure_ascii=False))
        return 0 if rep["expected_count_met"] else 2
    if a.cmd == "site":
        from .site import build
        print(build(a.envelopes, a.output), "company pages")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
