"""Batch runner: exactly one terminal envelope per input organisation number, checkpointed, with a run report."""
from __future__ import annotations

import json
import os
import re
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import AGENT_NAME, __version__
from .http import Client, now_iso
from .model import Envelope, SECTIONS, valid_orgnr
from .jobs import NavIndex, jobs
from .official import official
from .refresh import carry_forward, compare, load_previous
from .synthesis import synthesize, with_changes
from .website import extract_site, find_website


class CountingClient:
    """Per-company view on the shared client: counts this company's requests."""

    def __init__(self, client: Client):
        self.client = client
        self.requests = 0

    def get(self, *a, **kw):
        self.requests += 1
        return self.client.get(*a, **kw)


ORG_KEYS = ("organisation_number", "organization_number", "orgnr", "organisasjonsnummer", "org")
NINE = re.compile(r"(?<!\d)\d{3}\s?\d{3}\s?\d{3}(?!\d)")


def load_batch(path: str) -> tuple[list[str], int]:
    """Organisation numbers in input order without repeats. Accepts JSONL rows ({"organisation_number": ...}), a JSON
    list or {"organisation_numbers": [...]}, CSV with or without a header (first nine-digit field), or one number per
    line. A row that holds no usable number still gets its own envelope (state not_applicable); a header row does not."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    values = None
    if text.strip()[:1] in ("[", "{"):
        try:
            body = json.loads(text)
            values = body if isinstance(body, list) else next(
                (body[k] for k in ("organisation_numbers", "organization_numbers", "companies", "organisations")
                 if isinstance(body.get(k), list)), [body])
        except ValueError:
            values = None                              # JSON Lines: one object per line, read below
    if values is None:
        values = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                values.append(json.loads(line) if line[:1] in ("{", '"') else line)
    seen, out, dup = set(), [], 0
    for v in values:
        if isinstance(v, dict):
            raw = next((str(v[k]) for k in ORG_KEYS if v.get(k) not in (None, "")), "")
        else:
            raw = str(v)
        m = NINE.search(raw)
        if m:
            org = re.sub(r"\s", "", m.group(0))
        elif re.search(r"[A-Za-z]", raw) and not re.search(r"\d", raw):
            continue                                   # a header row such as «organisation_number»
        else:
            org = "".join(ch for ch in raw if not ch.isspace()) or "(empty)"
        if org in seen:
            dup += 1
            continue
        seen.add(org)
        out.append(org)
    return out, dup


class Pending:
    """A company whose hiring step waits for the Nav index. The worker goes on with the next company; `finish()`
    completes this one at the end of the queue, when the index has long been read."""

    def __init__(self, env: Envelope, cc: CountingClient, status: str, seconds: float, entity: dict, subunits: list):
        self.env, self.cc, self.status, self.seconds = env, cc, status, seconds
        self.entity, self.subunits = entity, subunits


def research(client: Client, orgnr: str, run_id: str, nav: NavIndex | None = None, defer: bool = False):
    """One company: official layer, Nav ads, website. Returns the finished envelope, or a `Pending` when `defer` is set
    and the Nav index is still loading (the website search then runs before the hiring step)."""
    t0 = time.monotonic()
    meta = {"run_id": run_id, "agent": AGENT_NAME, "agent_version": __version__, "started_at": now_iso()}
    env = Envelope(orgnr, meta)
    cc = CountingClient(client)
    status = "completed"
    later = None
    try:
        if not valid_orgnr(orgnr):
            for sec in SECTIONS:
                env.claim(sec, "organisation_number", None, [], "not_applicable",
                          note="input is not a valid Norwegian organisation number")
        else:
            off = official(cc, env)
            if off["entity"] is not None:
                if nav is not None and defer and not nav.ready():
                    later, hired = (off["entity"], off["subunits"]), {"homepages": []}
                else:
                    hired = jobs(cc, env, nav, off["entity"], off["subunits"]) if nav else {"homepages": []}
                extra = [(u, "nav_employer_homepage") for u, _ in hired["homepages"]]
                found = find_website(cc, env, off["entity"], extra=extra, subunits=off["subunits"])
                if found.get("status") == "available":
                    extract_site(cc, env, found)
    except Exception as exc:                       # one broken company must not break the batch
        status = "failed"
        env.error("agent", "%s: %s" % (type(exc).__name__, exc))
    if later is not None:
        return Pending(env, cc, status, time.monotonic() - t0, *later)
    return close(env, cc, status, time.monotonic() - t0)


def finish(p: Pending, nav: NavIndex) -> dict:
    nav.wait()
    t0 = time.monotonic()                          # waiting for the index is not this company's runtime
    status = p.status
    try:
        jobs(p.cc, p.env, nav, p.entity, p.subunits)
    except Exception as exc:
        status = "failed"
        p.env.error("agent", "%s: %s" % (type(exc).__name__, exc))
    return close(p.env, p.cc, status, p.seconds + time.monotonic() - t0)


def close(env: Envelope, cc: CountingClient, status: str, seconds: float) -> dict:
    env.run["completed_at"] = now_iso()
    env.run["terminal_status"] = status
    out = env.to_dict()
    try:
        out["synthesis"] = synthesize(env)
    except Exception as exc:
        out["synthesis"] = {"sentences": [], "error": "%s: %s" % (type(exc).__name__, exc)}
    out["operations"] = {"requests": cc.requests, "runtime_ms": int(seconds * 1000),
                         "third_party_cost_usd": round(getattr(env, "cost_usd", 0.0), 4)}
    return out


def failed_envelope(o: str, run_id: str, exc: Exception) -> dict:
    return {"organisation_number": o, "run": {"run_id": run_id, "terminal_status": "failed", "completed_at": now_iso()},
            "sections": {s: "failed" for s in SECTIONS}, "claims": [], "evidence": [], "source_snapshots": [],
            "changes": [], "errors": [{"stage": "runner", "message": str(exc)}],
            "operations": {"requests": 0, "runtime_ms": 0, "third_party_cost_usd": 0}}


def run_batch(organisations: str, output: str, report: str, run_id: str, workers: int = 8,
              expected_count: int | None = None, checkpoint: str | None = None, nav_days: int = 200,
              previous: str | None = None) -> dict:
    orgs, duplicates = load_batch(organisations)
    prev = load_previous(previous)
    for path in (output, report):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    started = now_iso()
    t0 = time.monotonic()
    client = Client()
    nav = NavIndex(client, days=nav_days) if nav_days else None
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
    if nav is not None and todo:
        nav.start()                                # reads every active Nav ad while the other layers work
    deferred = 0
    with open(checkpoint, "a", encoding="utf-8") as ck, ThreadPoolExecutor(max_workers=max(1, workers)) as pool:

        def record(o: str, env: dict) -> None:
            p = prev.get(o)
            carried = []
            env["changes"], env["history"] = compare(p, env, carried)
            carry_forward(env, carried)
            if env.get("synthesis") and "error" not in env["synthesis"]:
                env["synthesis"] = with_changes(env["synthesis"], env["changes"], p, env.get("claims") or [])
            env["refresh"] = {"previous_run_id": (p or {}).get("run", {}).get("run_id"),
                              "previous_completed_at": (p or {}).get("run", {}).get("completed_at"),
                              "material_changes": len(env["changes"]), "first_run": p is None,
                              "carried_forward": len(carried)}
            with lock:
                done[o] = env
                ck.write(json.dumps(env, ensure_ascii=False) + "\n")
                ck.flush()

        futures = {pool.submit(research, client, o, run_id, nav, True): o for o in todo}
        finishing = {}
        for fut in as_completed(futures):
            o = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:              # research() catches everything; this is the last net
                res = failed_envelope(o, run_id, exc)
            if isinstance(res, Pending):           # queued behind every company not yet started
                deferred += 1
                finishing[pool.submit(finish, res, nav)] = o
                continue
            record(o, res)
        for fut in as_completed(finishing):
            o = finishing[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = failed_envelope(o, run_id, exc)
            record(o, res)
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
        "third_party_cost_usd": round(sum(done[o]["operations"].get("third_party_cost_usd", 0) for o in orgs), 4),
        "nav_feed": ({"status": nav.status, "window_days": nav.days, "pages": nav.pages, "active_ads_listed": nav.listed,
                      "active_ads_read": nav.read, "unreadable": nav.missing, "closed_meanwhile": nav.closed,
                      "without_orgnr": nav.no_orgnr, "employer_orgnrs": len(nav.by_orgnr), "seconds": nav.seconds,
                      "time_limit_s": nav.budget_s, "stopped_by_time_limit": nav.out_of_time,
                      "hiring_steps_deferred": deferred} if nav else None),
    }
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    return rep
