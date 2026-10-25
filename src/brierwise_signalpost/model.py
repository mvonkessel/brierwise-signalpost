"""Company envelope: claims, claim-level evidence, availability states and section summaries."""
from __future__ import annotations

import hashlib
import json

STATES = ("available", "not_available", "blocked", "not_applicable", "ambiguous", "failed")
SECTIONS = (
    "legal_identity",            # 1 legal identity and public brand
    "annual_accounts",           # 2 latest annual accounts and available history
    "leadership_workplaces",     # 3 leadership and registered workplaces
    "website_profiles",          # 4 verified official website and company-owned profiles
    "hiring_activity",           # 5 hiring and dated public activity
)


def valid_orgnr(orgnr: str) -> bool:
    """Norwegian organisation number: nine digits, MOD11 check digit with weights 3 2 7 6 5 4 3 2."""
    if len(orgnr) != 9 or not orgnr.isdigit():
        return False
    s = sum(int(d) * w for d, w in zip(orgnr[:8], (3, 2, 7, 6, 5, 4, 3, 2)))
    check = 11 - s % 11
    if check == 11:
        check = 0
    return check != 10 and check == int(orgnr[8])


def state_of_status(status: int) -> str:
    """Availability state for an HTTP answer that carried no usable record."""
    if status in (404, 410):
        return "not_available"
    if status in (401, 403, 429, 451):
        return "blocked"
    return "failed"


class Envelope:
    def __init__(self, orgnr: str, run: dict):
        self.orgnr = orgnr
        self.run = dict(run)
        self.claims: list[dict] = []
        self.evidence: list[dict] = []
        self.errors: list[dict] = []
        self.changes: list[dict] = []
        self.sources: list[dict] = []          # source snapshots: one per fetched document
        self._ev_index: dict[tuple, str] = {}

    # evidence ---------------------------------------------------------------------------------------------------------
    def snapshot(self, response, source_class: str, extraction: str) -> dict:
        snap = {
            "source_url": response.final_url or response.url,
            "requested_url": response.url,
            "source_class": source_class,
            "http_status": response.status,
            "retrieved_at": response.retrieved_at,
            "content_sha256": response.sha256 if response.body else None,
            "extraction": extraction,
        }
        key = (snap["source_url"], snap["content_sha256"], snap["http_status"])
        if not any((s["source_url"], s["content_sha256"], s["http_status"]) == key for s in self.sources):
            self.sources.append(snap)
        return snap

    def cite(self, snap: dict, claim_span: str | None = None, locator: str | None = None) -> str:
        """Evidence id for a snapshot and a span inside it (deduplicated)."""
        key = (snap["source_url"], snap["content_sha256"], claim_span, locator)
        if key in self._ev_index:
            return self._ev_index[key]
        ev_id = "ev-%d" % (len(self.evidence) + 1)
        ev = {
            "id": ev_id,
            "source_url": snap["source_url"],
            "source_class": snap["source_class"],
            "retrieved_at": snap["retrieved_at"],
            "content_sha256": snap["content_sha256"],
            "extraction": snap["extraction"],
        }
        if claim_span is not None:
            ev["claim_span"] = str(claim_span)[:400]
        if locator:
            ev["locator"] = locator
        self.evidence.append(ev)
        self._ev_index[key] = ev_id
        return ev_id

    # claims -----------------------------------------------------------------------------------------------------------
    def claim(self, section: str, field: str, value, evidence_ids: list[str], availability: str = "available",
              confidence: float = 0.99, reporting_period: str | None = None, effective_date: str | None = None,
              note: str | None = None) -> dict:
        assert availability in STATES and section in SECTIONS
        if availability != "available":
            value = None                       # never turn absence into a value, never into zero
        c = {"section": section, "field": field, "value": value, "availability": availability,
             "confidence": round(confidence, 3), "evidence_ids": list(evidence_ids)}
        if reporting_period:
            c["reporting_period"] = reporting_period
        if effective_date:
            c["effective_date"] = effective_date
        if note:
            c["note"] = note
        self.claims.append(c)
        return c

    def error(self, stage: str, message: str, url: str | None = None) -> None:
        e = {"stage": stage, "message": str(message)[:300]}
        if url:
            e["url"] = url
        self.errors.append(e)

    # output -----------------------------------------------------------------------------------------------------------
    def section_states(self) -> dict:
        out = {}
        for sec in SECTIONS:
            states = [c["availability"] for c in self.claims if c["section"] == sec]
            if "available" in states:
                out[sec] = "available"
            elif not states:
                out[sec] = "not_available"
            else:
                for s in ("ambiguous", "blocked", "failed", "not_applicable", "not_available"):
                    if s in states:
                        out[sec] = s
                        break
        return out

    def to_dict(self) -> dict:
        return {
            "organisation_number": self.orgnr,
            "run": self.run,
            "sections": self.section_states(),
            "claims": self.claims,
            "evidence": self.evidence,
            "source_snapshots": self.sources,
            "changes": self.changes,
            "errors": self.errors,
        }


def content_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
