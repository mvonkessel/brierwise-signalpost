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
ACCOUNT_FIELDS = {"revenue", "operating_result", "net_result", "equity", "total_assets", "total_debt",
                  "profit_before_tax"}
# field -> the claim whose failed or blocked state in this run means the field could not be re-checked
SOURCE_OF = {"official_website": "official_website", "public_brand": "official_website",
             "company_profile": "official_website", "dated_activity": "official_website"}
SOURCE_OF.update({f: "annual_accounts" for f in ACCOUNT_FIELDS})


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
        if f in ACCOUNT_FIELDS:
            key = (f, (v or {}).get("accounts_type"), c.get("reporting_period"))
        elif f in MATERIAL:
            ident = MATERIAL[f]
            key = (f, json.dumps(ident(v), ensure_ascii=False, default=str) if ident else None)
        else:
            continue
        out[key] = c
    return out


def compare(prev: dict | None, cur: dict, carried: list | None = None) -> tuple[list, list]:
    """Return (changes, history) for one company. Facts whose source failed in this run are appended to `carried`
    (previous claim plus its evidence) instead of being reported as removed."""
    if not prev:
        return [], []
    old, new = _facts(prev), _facts(cur)
    pev = {e["id"]: e for e in prev.get("evidence", [])}
    same_version = (prev.get("run") or {}).get("agent_version") == (cur.get("run") or {}).get("agent_version")
    failed = _failed_sources(cur)
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
            changes.append({"field": field, "change": "added", "before": None, "after": b["value"],
                            "reporting_period": b.get("reporting_period"), "evidence_ids": b["evidence_ids"],
                            "previous_run": prev.get("run", {}).get("run_id")})
        else:
            if SOURCE_OF.get(field) in failed and carried is not None:
                carried.append({"claim": a, "evidence": [pev[i] for i in a["evidence_ids"] if i in pev],
                                "run_id": prev.get("run", {}).get("run_id"), "failed_source": SOURCE_OF[field]})
                continue                      # not re-checked in this run: kept, not removed
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
