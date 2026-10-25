"""Hiring layer: active job ads from Nav's official job vacancy feed (pam-stilling-feed), matched by organisation number.

Terms (arbeidsplassen.nav.no/vilkar-api): anyone may use and republish the ads; inactive ads must be removed at once;
contact persons are personal data and are not used. Matching: candidate ads by employer name from the feed summary,
then the full entry must carry the company's organisation number or one of its registered subunits.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from .model import Envelope
from .website import LEGAL_SUFFIX, fold

FEED = "https://pam-stilling-feed.nav.no"
TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9._-]+")


def norm_name(name: str) -> str:
    n = LEGAL_SUFFIX.sub("", (name or "").strip()).strip(" ,.-")
    return " ".join(re.split(r"[^a-z0-9]+", fold(n))).strip()


CORE_CUT = re.compile(r"\s(as|asa|ans|da|sa|avd|avdeling|filial|dept)(\s|$)")


def core_name(name: str) -> str:
    """Name before a legal form or department word: «PECUS AS AVD OSLO» and «PECUS AS» both give «pecus»."""
    n = " " + " ".join(re.split(r"[^a-z0-9]+", fold(name or ""))).strip() + " "
    m = CORE_CUT.search(n)
    return (n[: m.start()] if m else n).strip()


class NavIndex:
    """Latest state of every ad changed in the last `days` days, keyed by normalised employer name."""

    def __init__(self, client, days: int = 45, max_pages: int = 400):
        self.client, self.days, self.max_pages = client, days, max_pages
        self.by_name: dict[str, dict[str, dict]] = {}
        self.token = None
        self.status = "not_loaded"
        self.pages = 0
        self.snapshot = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self.status != "not_loaded":
                return
            t0 = time.monotonic()
            try:
                self._load()
            except Exception as exc:               # never retried within a run: one failed load is the answer
                self.error = "%s: %s" % (type(exc).__name__, exc)
                self.status = "partial" if self.by_name else "failed"
            self.seconds = round(time.monotonic() - t0, 1)

    def _load(self) -> None:
        if True:
            r = self.client.get(FEED + "/api/publicToken", accept="text/plain")
            m = TOKEN_RE.search(r.text()) if r.ok else None
            if not m:
                self.status = "blocked" if r.status in (401, 403) else "failed"
                return
            self.token = m.group(0)              # public token, rotated by Nav; never written to disk
            since = format_datetime(datetime.now(timezone.utc) - timedelta(days=self.days), usegmt=True)
            url = FEED + "/api/v1/feed"
            first = True
            while url and self.pages < self.max_pages:
                hdr = {"Authorization": "Bearer " + self.token}
                if first:
                    hdr["If-Modified-Since"] = since
                r = self.client.get(url, accept="application/json", headers=hdr, timeout=30, retries=2)
                if not r.ok:
                    self.status = "partial" if self.pages else ("blocked" if r.status in (401, 403) else "failed")
                    break
                first = False
                self.pages += 1
                data = r.json()
                if self.snapshot is None:
                    self.snapshot = r
                for it in data.get("items") or []:
                    fe = it.get("_feed_entry") or {}
                    biz = fe.get("businessName") or ""
                    entry = {"uuid": fe.get("uuid"), "status": fe.get("status"), "title": fe.get("title"),
                             "municipal": fe.get("municipal"), "changed": fe.get("sistEndret"),
                             "url": FEED + it.get("url", "")}
                    for key in {norm_name(biz), core_name(biz)}:
                        if key:
                            self.by_name.setdefault(key, {})[entry["uuid"]] = entry    # later entries are newer
                nxt = data.get("next_url")
                url = FEED + nxt if nxt else None
            if self.status == "not_loaded":
                self.status = "loaded"

    def candidates(self, names: list[str]) -> list[dict]:
        out = {}
        for n in names:
            for key in {norm_name(n), core_name(n)}:
                for uuid, e in (self.by_name.get(key) or {}).items():
                    if e["status"] == "ACTIVE":
                        out[uuid] = e
        return sorted(out.values(), key=lambda e: e.get("changed") or "", reverse=True)


def jobs(client, env: Envelope, index: NavIndex, entity: dict, subunits: list[dict], limit: int = 12) -> dict:
    index.load()
    if index.status in ("blocked", "failed"):
        env.claim("hiring_activity", "job_postings", None, [], index.status,
                  note="Nav job vacancy feed could not be read in this run")
        return {"jobs": 0, "homepages": []}
    names = [entity.get("navn") or ""] + [u.get("name") or "" for u in subunits]
    own = {env.orgnr} | {u.get("organisation_number") for u in subunits if u.get("organisation_number")}
    found, homepages, checked = 0, [], 0
    for cand in index.candidates(names)[:limit]:
        r = client.get(cand["url"], accept="application/json", headers={"Authorization": "Bearer " + index.token},
                       timeout=20, retries=1)
        checked += 1
        if not r.ok:
            continue
        try:
            d = r.json()
        except ValueError:
            continue
        if d.get("status") != "ACTIVE":
            continue
        ad = d.get("ad_content") or d.get("json") or {}
        emp = ad.get("employer") or {}
        if str(emp.get("orgnr") or "") not in own:
            continue
        snap = env.snapshot(r, "official_job_feed", "nav_feedentry_json")
        locs = [", ".join(x for x in (w.get("city"), w.get("municipal"), w.get("county")) if x)
                for w in ad.get("workLocations") or []]
        value = {"title": ad.get("title"), "job_title": ad.get("jobtitle"), "published": (ad.get("published") or "")[:10],
                 "expires": (ad.get("expires") or "")[:10] or None, "application_due": ad.get("applicationDue"),
                 "locations": [l for l in locs if l], "employer_name": emp.get("name"),
                 "employer_organisation_number": emp.get("orgnr"), "positions": ad.get("positioncount"),
                 "engagement": ad.get("engagementtype"), "extent": ad.get("extent"), "url": ad.get("link"),
                 "source": "Nav job vacancy feed"}
        env.claim("hiring_activity", "job_posting", {k: v for k, v in value.items() if v not in (None, "", [])},
                  [env.cite(snap, "%s; employer orgnr %s; published %s" % (ad.get("title"), emp.get("orgnr"),
                                                                          value["published"]), "nav ad")],
                  effective_date=value["published"] or None)
        found += 1
        if emp.get("homepage"):
            homepages.append((emp.get("homepage"), snap))
    if found == 0:
        ev = []
        if index.snapshot is not None:
            snap = env.snapshot(index.snapshot, "official_job_feed", "nav_feed_page")
            ev = [env.cite(snap, "first feed page of %d read in this run (changes since %d days ago)" %
                           (index.pages, index.days), "nav feed")]
        env.claim("hiring_activity", "job_postings", None, ev, "not_available",
                  note="no active ad with this organisation number among %d ads changed in the last %d days on Nav's "
                       "official feed (%d name matches checked)" % (len({u for v in index.by_name.values() for u in v}),
                                                                     index.days, checked))
    return {"jobs": found, "homepages": homepages}
