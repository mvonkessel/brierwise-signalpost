"""Batch runner: exactly one terminal envelope per input organisation number, checkpointed, with a run report."""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import AGENT_NAME, __version__
from .http import Client, now_iso
from .model import Envelope, SECTIONS, valid_orgnr
from .official import official
from .website import extract_site, find_website


class CountingClient:
    """Per-company view on the shared client: counts this company's requests."""

    def __init__(self, client: Client):
        self.client = client
        self.requests = 0

    def get(self, *a, **kw):
        self.requests += 1
        return self.client.get(*a, **kw)


def load_batch(path: str) -> tuple[list[str], int]:
    """Organisation numbers in input order without repeats; JSONL rows (organisation_number) or one number per line."""
    seen, out, dup = set(), [], 0
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            org = None
            if line.startswith("{"):
                row = json.loads(line)
                for key in ("organisation_number", "organization_number", "orgnr", "organisasjonsnummer", "org"):
                    if row.get(key):
                        org = str(row[key])
                        break
            elif line.startswith('"'):
                org = json.loads(line)
            else:
                org = line
            org = "".join(ch for ch in str(org or "") if not ch.isspace())
            if org in seen:
                dup += 1
                continue
            seen.add(org)
            out.append(org)
    return out, dup


def research(client: Client, orgnr: str, run_id: str) -> dict:
    t0 = time.monotonic()
    meta = {"run_id": run_id, "agent": AGENT_NAME, "agent_version": __version__, "started_at": now_iso()}
    env = Envelope(orgnr, meta)
    cc = CountingClient(client)
    status = "completed"
    try:
        if not valid_orgnr(orgnr):
            for sec in SECTIONS:
                env.claim(sec, "organisation_number", None, [], "not_applicable",
                          note="input is not a valid Norwegian organisation number")
        else:
            off = official(cc, env)
            if off["entity"] is not None:
                found = find_website(cc, env, off["entity"])
                if found.get("status") == "available":
                    extract_site(cc, env, found)
    except Exception as exc:                       # one broken company must not break the batch
        status = "failed"
        env.error("agent", "%s: %s" % (type(exc).__name__, exc))
    env.run["completed_at"] = now_iso()
    env.run["terminal_status"] = status
    out = env.to_dict()
    out["operations"] = {"requests": cc.requests, "runtime_ms": int((time.monotonic() - t0) * 1000),
                         "third_party_cost_usd": 0}
    return out


def run_batch(organisations: str, output: str, report: str, run_id: str, workers: int = 8,
              expected_count: int | None = None, checkpoint: str | None = None) -> dict:
    orgs, duplicates = load_batch(organisations)
    for path in (output, report):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    started = now_iso()
    t0 = time.monotonic()
    client = Client()
    checkpoint = checkpoint or output + ".partial"
    done = {}
    if os.path.exists(checkpoint):                 # resume: keep finished envelopes of this run id
        with open(checkpoint, encoding="utf-8") as fh:
            for line in fh:
                try:
                    env = json.loads(line)
                except ValueError:
                    continue
                if env.get("run", {}).get("run_id") == run_id:
                    done[env["organisation_number"]] = env
    lock = threading.Lock()
    todo = [o for o in orgs if o not in done]
    with open(checkpoint, "a", encoding="utf-8") as ck, ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(research, client, o, run_id): o for o in todo}
        for fut in as_completed(futures):
            o = futures[fut]
            try:
                env = fut.result()
            except Exception as exc:              # research() catches everything; this is the last net
                env = {"organisation_number": o, "run": {"run_id": run_id, "terminal_status": "failed",
                                                        "completed_at": now_iso()},
                       "sections": {s: "failed" for s in SECTIONS}, "claims": [], "evidence": [],
                       "source_snapshots": [], "changes": [], "errors": [{"stage": "runner", "message": str(exc)}],
                       "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0}}
            with lock:
                done[o] = env
                ck.write(json.dumps(env, ensure_ascii=False) + "\n")
                ck.flush()
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    tmp = output + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for o in orgs:
            fh.write(json.dumps(done[o], ensure_ascii=False) + "\n")
    os.replace(tmp, output)
    if os.path.exists(checkpoint):
        os.remove(checkpoint)
    rt = sorted(done[o]["operations"]["runtime_ms"] for o in orgs) or [0]
    sections = {s: Counter(done[o]["sections"].get(s, "missing") for o in orgs) for s in SECTIONS}
    rep = {
        "run_id": run_id, "agent": AGENT_NAME, "agent_version": __version__,
        "started_at": started, "completed_at": now_iso(), "wall_seconds": round(time.monotonic() - t0, 1),
        "input_rows_unique": len(orgs), "input_duplicates_skipped": duplicates,
        "envelopes": len(orgs), "expected_count": expected_count,
        "expected_count_met": (expected_count is None or expected_count == len(orgs)),
        "terminal_status": dict(Counter(done[o]["run"]["terminal_status"] for o in orgs)),
        "section_states": {s: dict(c) for s, c in sections.items()},
        "claims": sum(len(done[o]["claims"]) for o in orgs),
        "requests_by_host": dict(client.requests), "failed_requests_by_host": dict(client.failures),
        "requests_total": sum(client.requests.values()),
        "runtime_ms_p50": int(statistics.median(rt)), "runtime_ms_p95": rt[min(len(rt) - 1, int(0.95 * len(rt)))],
        "third_party_cost_usd": 0,
    }
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep
