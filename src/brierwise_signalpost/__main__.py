"""Command line: python -m brierwise_signalpost run --organisations BATCH.jsonl --output envelopes.jsonl --report report.json"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from .run import run_batch


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="brierwise_signalpost")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="research a batch of organisation numbers")
    r.add_argument("--organisations", required=True, help="JSONL with organisation_number, or one number per line")
    r.add_argument("--output", required=True, help="output JSONL: one terminal envelope per input")
    r.add_argument("--report", required=True, help="machine-readable run report (JSON)")
    r.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ"))
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--expected-count", type=int)
    r.add_argument("--site", help="also build the static profile site into this folder")
    r.add_argument("--previous", help="envelopes of the previous run (JSONL): changes and history are derived from it")
    r.add_argument("--nav-days", type=int, default=45, help="Nav job feed window in days (0 switches it off)")
    s = sub.add_parser("site", help="build the static profile site from envelopes")
    s.add_argument("--envelopes", required=True)
    s.add_argument("--output", required=True)
    a = p.parse_args(argv)
    if a.cmd == "run":
        rep = run_batch(a.organisations, a.output, a.report, a.run_id, a.workers, a.expected_count,
                        nav_days=a.nav_days, previous=a.previous)
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
