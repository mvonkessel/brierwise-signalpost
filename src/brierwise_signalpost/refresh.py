"""Refresh: compare a new envelope with the previous one, expose material changes, preserve prior evidence.

Idempotent by construction: only claim values are compared (never retrieval times or hashes), so the same source
snapshot yields no change. Facts that disappear are kept under `history` together with their original evidence. When
the source of a fact failed in this run (website unreachable, accounts service down), the fact is not reported as
removed: the last supported value is carried forward as a claim marked `carried_forward`, with its original evidence
and retrieval time, next to the failure. Between two agent versions only the value fields both versions write are
compared, so a new output field is not a change of the company.
"""
from __future__ import annotations

import json

from .official import ACCOUNT_FIELDS as _ACCOUNTS
from .website import profile_url

# field -> how to identify one fact inside a multi-valued field (None: single-valued field)
MATERIAL = {
    "legal_name": None, "organisation_form": None, "registry_status": None, "business_address": None,
    "postal_address": None, "industry_codes": None, "registered_employees": None, "registry_website": None,
    "official_website": None, "public_brand": None, "share_capital": None, "registered_purpose": None, "part_of_group": None,
    "role": lambda v: (v.get("role_code"), v.get("name")),
    "registered_workplace": lambda v: v.get("organisation_number"),
    "company_profile": lambda v: (v.get("platform"), v.get("url")),
    "job_posting": lambda v: v.get("nav_ad_url") or v.get("url") or (v.get("title"), v.get("published")),
    "dated_activity": lambda v: (v.get("date"), v.get("title")),
    "register_event": lambda v: (v.get("date"), v.get("event")),
}
ACCOUNT_FIELDS = {name for name, _ in _ACCOUNTS}      # one fact per (field, accounts type, reporting period)
# Dated items that drop out of a sliding window (the register's last 13 months, a site's latest posts) did not stop
# having happened: they go to history, not to the changes.
DATED = {"register_event", "dated_activity"}
# field -> the claim whose failed or blocked state in this run means the field could not be re-checked
SOURCE_OF = {"official_website": "official_website", "public_brand": "official_website",
             "company_profile": "official_website", "dated_activity": "official_website"}
SOURCE_OF.update({f: "annual_accounts" for f in ACCOUNT_FIELDS})
SOURCE_OF.update({"job_posting": "job_postings", "register_event": "register_events", "role": "roles",
                  "registered_workplace": "registered_workplaces"})
SOURCE_OF.update({f: "legal_name" for f in ("organisation_form", "registry_status", "business_address", "postal_address",
                                            "industry_codes", "registered_employees", "registry_website",
                                            "share_capital", "registered_purpose", "part_of_group")})


def _comparable(a, b, same_version: bool):
    """Values as compared: whole values within one agent version, common keys of two dict values across versions."""
    if same_version or not (isinstance(a, dict) and isinstance(b, dict)):
        return a, b
    keys = set(a) & set(b)
    return {k: a[k] for k in keys}, {k: b[k] for k in keys}


def _failed_sources(env: dict) -> set:
    return {c["field"] for c in env.get("claims", []) if c.get("availability") in ("failed", "blocked")}


def _facts(env: dict) -> dict:
    out = {}
    for c in env.get("claims", []):
        if c.get("availability") != "available":
            continue
        f, v = c["field"], c["value"]
        if f == "company_profile" and isinstance(v, dict) and v.get("url"):
            # compared in canonical form, so an address an older version wrote differently is the same account
            v = dict(v, url=profile_url(v["url"]))
            c = dict(c, value=v)
        if f in ACCOUNT_FIELDS:
            key = (f, (v or {}).get("accounts_type"), c.get("reporting_period"))
        elif f in MATERIAL:
            ident = MATERIAL[f]
            key = (f, json.dumps(ident(v), ensure_ascii=False, default=str) if ident else None)
        else:
            continue
        out[key] = c
    return out


def compare(prev: dict | None, cur: dict, carried: list | None = None, observed: list | None = None) -> tuple[list, list]:
    """Return (changes, history) for one company. Facts whose source failed in this run are appended to `carried`
    (previous claim plus its evidence) instead of being reported as removed. Facts that are new only because their
    source failed in the previous run, or because a newer agent version writes a field the previous one did not, are
    appended to `observed` instead of being reported as added: the company did not change."""
    if not prev:
        return [], []
    old, new = _facts(prev), _facts(cur)
    pev = {e["id"]: e for e in prev.get("evidence", [])}
    same_version = (prev.get("run") or {}).get("agent_version") == (cur.get("run") or {}).get("agent_version")
    failed, prev_failed = _failed_sources(cur), _failed_sources(prev)
    prev_fields = {c.get("field") for c in prev.get("claims", [])}
    changes, history = [], []
    for key in sorted(set(old) | set(new), key=str):
        a, b = old.get(key), new.get(key)
        field = key[0]
        if a and b:
            va, vb = _comparable(a["value"], b["value"], same_version)
            if json.dumps(va, sort_keys=True, default=str) != json.dumps(vb, sort_keys=True, default=str):
                changes.append({"field": field, "change": "changed", "before": a["value"], "after": b["value"],
                                "reporting_period": b.get("reporting_period"), "evidence_ids": b["evidence_ids"],
                                "previous_run": prev.get("run", {}).get("run_id")})
                history.append({"claim": a, "evidence": [pev[i] for i in a["evidence_ids"] if i in pev],
                                "run_id": prev.get("run", {}).get("run_id")})
        elif b:
            why = ("its source (%s) failed in the previous run" % SOURCE_OF[field] if SOURCE_OF.get(field) in
                   prev_failed else "the previous agent version did not write this field" if not same_version and
                   field not in prev_fields else None)
            if why:
                if observed is not None:
                    observed.append({"field": field, "reporting_period": b.get("reporting_period"),
                                     "evidence_ids": b["evidence_ids"], "reason": why})
                continue                      # first observed, not a change of the company
            changes.append({"field": field, "change": "added", "before": None, "after": b["value"],
                            "reporting_period": b.get("reporting_period"), "evidence_ids": b["evidence_ids"],
                            "previous_run": prev.get("run", {}).get("run_id")})
        else:
            if SOURCE_OF.get(field) in failed and carried is not None:
                carried.append({"claim": a, "evidence": [pev[i] for i in a["evidence_ids"] if i in pev],
                                "run_id": prev.get("run", {}).get("run_id"), "failed_source": SOURCE_OF[field]})
                continue                      # not re-checked in this run: kept, not removed
            if field in ACCOUNT_FIELDS or field in DATED:
                history.append({"claim": a, "evidence": [pev[i] for i in a["evidence_ids"] if i in pev],
                                "run_id": prev.get("run", {}).get("run_id")})
                continue                      # older periods are preserved, not reported as removed
            changes.append({"field": field, "change": "removed", "before": a["value"], "after": None,
                            "evidence_ids": [], "previous_evidence": [pev[i] for i in a["evidence_ids"] if i in pev],
                            "previous_run": prev.get("run", {}).get("run_id")})
            history.append({"claim": a, "evidence": [pev[i] for i in a["evidence_ids"] if i in pev],
                            "run_id": prev.get("run", {}).get("run_id")})
    history = (prev.get("history") or []) + history
    return changes, history


def carry_forward(env: dict, carried: list) -> int:
    """Add the carried facts to the envelope as claims marked `carried_forward`, with copies of their evidence."""
    n = 0
    for item in carried:
        ids = []
        for ev in item["evidence"]:
            e = dict(ev, id="ev-%d" % (len(env.setdefault("evidence", [])) + 1), carried_from_run=item["run_id"])
            env["evidence"].append(e)
            ids.append(e["id"])
        c = dict(item["claim"], evidence_ids=ids, carried_forward=True,
                 confidence=round(float(item["claim"].get("confidence") or 0.9) * 0.9, 3),
                 note=("last supported value from run %s, kept because its source (%s) failed in this run; "
                       "not re-checked" % (item["run_id"], item["failed_source"])))
        env.setdefault("claims", []).append(c)
        n += 1
    return n


def load_previous(path: str | None) -> dict:
    prev = {}
    if not path:
        return prev
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                env = json.loads(line)
                prev[env["organisation_number"]] = env
    return prev
