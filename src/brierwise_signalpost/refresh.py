"""Refresh: compare a new envelope with the previous one, expose material changes, preserve prior evidence.

Idempotent by construction: only claim values are compared (never retrieval times or hashes), so the same source
snapshot yields no change. Facts that disappear are kept under `history` together with their original evidence.
"""
from __future__ import annotations

import json

# field -> how to identify one fact inside a multi-valued field (None: single-valued field)
MATERIAL = {
    "legal_name": None, "organisation_form": None, "registry_status": None, "business_address": None,
    "postal_address": None, "industry_codes": None, "registered_employees": None, "registry_website": None,
    "official_website": None, "share_capital": None, "registered_purpose": None, "part_of_group": None,
    "role": lambda v: (v.get("role_code"), v.get("name")),
    "registered_workplace": lambda v: v.get("organisation_number"),
    "company_profile": lambda v: (v.get("platform"), v.get("url")),
    "job_posting": lambda v: v.get("url") or (v.get("title"), v.get("published")),
    "dated_activity": lambda v: (v.get("date"), v.get("title")),
}
ACCOUNT_FIELDS = {"revenue", "operating_result", "net_result", "equity", "total_assets", "total_debt",
                  "profit_before_tax"}


def _facts(env: dict) -> dict:
    out = {}
    for c in env.get("claims", []):
        if c.get("availability") != "available":
            continue
        f, v = c["field"], c["value"]
        if f in ACCOUNT_FIELDS:
            key = (f, (v or {}).get("accounts_type"), c.get("reporting_period"))
        elif f in MATERIAL:
            ident = MATERIAL[f]
            key = (f, json.dumps(ident(v), ensure_ascii=False, default=str) if ident else None)
        else:
            continue
        out[key] = c
    return out


def compare(prev: dict | None, cur: dict) -> tuple[list, list]:
    """Return (changes, history) for one company."""
    if not prev:
        return [], []
    old, new = _facts(prev), _facts(cur)
    pev = {e["id"]: e for e in prev.get("evidence", [])}
    changes, history = [], []
    for key in sorted(set(old) | set(new), key=str):
        a, b = old.get(key), new.get(key)
        field = key[0]
        if a and b:
            if json.dumps(a["value"], sort_keys=True, default=str) != json.dumps(b["value"], sort_keys=True,
                                                                                default=str):
                changes.append({"field": field, "change": "changed", "before": a["value"], "after": b["value"],
                                "reporting_period": b.get("reporting_period"), "evidence_ids": b["evidence_ids"],
                                "previous_run": prev.get("run", {}).get("run_id")})
                history.append({"claim": a, "evidence": [pev[i] for i in a["evidence_ids"] if i in pev],
                                "run_id": prev.get("run", {}).get("run_id")})
        elif b:
            changes.append({"field": field, "change": "added", "before": None, "after": b["value"],
                            "reporting_period": b.get("reporting_period"), "evidence_ids": b["evidence_ids"],
                            "previous_run": prev.get("run", {}).get("run_id")})
        else:
            if field in ACCOUNT_FIELDS:
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
