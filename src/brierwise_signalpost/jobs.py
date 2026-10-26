"""Hiring layer: active job ads from Nav's official job vacancy feed (pam-stilling-feed), matched by organisation number.

Terms (arbeidsplassen.nav.no/vilkar-api): anyone may use and republish the ads; inactive ads must be removed at once;
contact persons are personal data and are not used. The feed summary names the employer only by its display name; the
organisation number (in Nav ads the registered workplace, a subunit) is in the full entry. Matching the display name to
the legal name found 105 of 199 ads of universe companies in a random sample of 400 active ads (26 October 2026), so
the index reads every active entry once per run, in a background thread while the other layers work, and matches by
organisation number only.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from .model import Envelope
from .website import LEGAL_SUFFIX, fold

FEED = "https://pam-stilling-feed.nav.no"
AD_PAGE = "https://arbeidsplassen.nav.no/stillinger/stilling/"
TOKEN_RE = re.compile(r"eyJ[A-Za-z0-9._-]+")
# Absence of ads is reported only when the index read nearly every active entry; otherwise the state is failed.
MAX_MISSING_SHARE = 0.02


def norm_name(name: str) -> str:
    n = LEGAL_SUFFIX.sub("", (name or "").strip()).strip(" ,.-")
    return " ".join(re.split(r"[^a-z0-9]+", fold(n))).strip()


CORE_CUT = re.compile(r"\s(as|asa|ans|da|sa|avd|avdeling|filial|dept)(\s|$)")


def core_name(name: str) -> str:
    """Name before a legal form or department word: «PECUS AS AVD OSLO» and «PECUS AS» both give «pecus»."""
    n = " " + " ".join(re.split(r"[^a-z0-9]+", fold(name or ""))).strip() + " "
    m = CORE_CUT.search(n)
    return (n[: m.start()] if m else n).strip()


def ad_of(resp) -> dict:
    d = resp.json()
    return d.get("ad_content") or d.get("json") or {}


class NavIndex:
    """Every ad that Nav's feed lists as active (latest state of the changes of the last `days` days), keyed by the
    employer's organisation number. `start()` loads it in a background thread; `wait()` blocks until it is ready."""

    def __init__(self, client, days: int = 200, max_pages: int = 800, workers: int = 12, budget_s: float = 900.0):
        self.client, self.days, self.max_pages, self.workers = client, days, max_pages, workers
        self.budget_s = budget_s         # wall-clock limit for the whole index: a slow feed must not hold the batch
        self.out_of_time = False
        self.by_orgnr: dict[str, list] = {}
        self.token = None
        self.status = "not_loaded"
        self.pages = 0
        self.listed = 0                  # active ads in the feed window
        self.read = 0                    # full entries read, still active, with an employer organisation number
        self.missing = 0                 # active ads whose full entry could not be read
        self.no_orgnr = 0
        self.closed = 0                  # listed as active, closed by the time the entry was read
        self.snapshot = None
        self.error = None
        self.seconds = None
        self._lock = threading.Lock()
        self._ready = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self.load, name="nav-index", daemon=True).start()

    def ready(self) -> bool:
        return self._ready.is_set()

    def wait(self) -> None:
        self.load()                      # returns at once when another thread loads or has loaded the index
        self._ready.wait()

    def load(self) -> None:
        with self._lock:
            if self.status != "not_loaded":
                return
            self.status = "loading"
        t0 = time.monotonic()
        self.deadline = t0 + self.budget_s
        try:
            self._load()
        except Exception as exc:         # never retried within a run: one failed load is the answer
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.status = "partial" if self.by_orgnr else "failed"
        self.seconds = round(time.monotonic() - t0, 1)
        self._ready.set()

    def _load(self) -> None:
        r = self.client.get(FEED + "/api/publicToken", accept="text/plain")
        m = TOKEN_RE.search(r.text()) if r.ok else None
        if not m:
            self.status = "blocked" if r.status in (401, 403) else "failed"
            return
        self.token = m.group(0)          # public token, rotated by Nav; never written to disk
        auth = {"Authorization": "Bearer " + self.token}
        since = format_datetime(datetime.now(timezone.utc) - timedelta(days=self.days), usegmt=True)
        latest, url, first = {}, FEED + "/api/v1/feed", True
        while url and self.pages < self.max_pages:
            if time.monotonic() > self.deadline:
                self.out_of_time = True
                break
            hdr = dict(auth)
            if first:
                hdr["If-Modified-Since"] = since
            r = self.client.get(url, accept="application/json", headers=hdr, timeout=30, retries=2)
            if not r.ok:
                if not self.pages:
                    self.status = "blocked" if r.status in (401, 403) else "failed"
                    return
                break
            first = False
            self.pages += 1
            data = r.json()
            if self.snapshot is None:
                self.snapshot = r
            for it in data.get("items") or []:
                fe = it.get("_feed_entry") or {}
                if fe.get("uuid"):
                    latest[fe["uuid"]] = (fe.get("status"), FEED + it.get("url", ""))    # later entries are newer
            nxt = data.get("next_url")
            url = FEED + nxt if nxt else None
        feed_complete = url is None
        active = [u for u, (st, _) in latest.items() if st == "ACTIVE"]
        self.listed = len(active)

        def fetch(uuid):
            if time.monotonic() > self.deadline:
                self.out_of_time = True
                return None
            return self.client.get(latest[uuid][1], accept="application/json", headers=auth, timeout=20, retries=2)

        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as pool:
            for resp in pool.map(fetch, active):
                try:
                    if resp is None or not resp.ok:
                        raise ValueError("not read" if resp is None else "HTTP %s" % resp.status)
                    d = resp.json()
                except ValueError:
                    self.missing += 1
                    continue
                if d.get("status") != "ACTIVE":
                    self.closed += 1
                    continue
                ad = d.get("ad_content") or d.get("json") or {}
                org = str((ad.get("employer") or {}).get("orgnr") or "")
                if not org:
                    self.no_orgnr += 1
                    continue
                self.read += 1
                self.by_orgnr.setdefault(org, []).append(resp)
        complete = feed_complete and self.missing <= MAX_MISSING_SHARE * max(1, self.listed)
        self.status = "loaded" if complete else "partial"

    def ads_for(self, orgnrs) -> list:
        return [r for o in orgnrs for r in self.by_orgnr.get(o, [])]


def jobs(client, env: Envelope, index: NavIndex, entity: dict, subunits: list[dict], limit: int = 30) -> dict:
    index.wait()
    if index.status in ("blocked", "failed"):
        env.claim("hiring_activity", "job_postings", None, [], index.status,
                  note="Nav job vacancy feed could not be read in this run")
        return {"jobs": 0, "homepages": []}
    own = {env.orgnr} | {u.get("organisation_number") for u in subunits if u.get("organisation_number")}
    ads = []
    for r in index.ads_for(sorted(own)):
        try:
            ads.append((r, ad_of(r)))
        except ValueError:
            continue
    ads.sort(key=lambda x: (x[1].get("published") or "", x[0].url), reverse=True)
    found, homepages = 0, []
    for r, ad in ads[:limit]:
        emp = ad.get("employer") or {}
        snap = env.snapshot(r, "official_job_feed", "nav_feedentry_json")
        locs = [", ".join(x for x in (w.get("city"), w.get("municipal"), w.get("county")) if x)
                for w in ad.get("workLocations") or []]
        uuid = ad.get("uuid") or r.url.rsplit("/", 1)[-1]
        value = {"title": ad.get("title"), "job_title": ad.get("jobtitle"), "published": (ad.get("published") or "")[:10],
                 "expires": (ad.get("expires") or "")[:10] or None, "application_due": ad.get("applicationDue"),
                 "locations": [l for l in locs if l], "employer_name": emp.get("name"),
                 "employer_organisation_number": emp.get("orgnr"), "positions": ad.get("positioncount"),
                 "engagement": ad.get("engagementtype"), "extent": ad.get("extent"), "url": ad.get("link"),
                 "nav_ad_url": AD_PAGE + uuid if uuid and AD_PAGE + uuid != ad.get("link") else None,
                 "source": "Nav job vacancy feed"}
        env.claim("hiring_activity", "job_posting", {k: v for k, v in value.items() if v not in (None, "", [])},
                  [env.cite(snap, "%s; employer orgnr %s; published %s" % (ad.get("title"), emp.get("orgnr"),
                                                                          value["published"]), "nav ad")],
                  effective_date=value["published"] or None)
        found += 1
        if emp.get("homepage"):
            homepages.append((emp.get("homepage"), snap))
    feed_ev = []
    if index.snapshot is not None:
        snap = env.snapshot(index.snapshot, "official_job_feed", "nav_feed_page")
        feed_ev = [env.cite(snap, "first feed page of %d read in this run (changes since %d days ago); %d active ads "
                                  "read in full" % (index.pages, index.days, index.read), "nav feed")]
    if len(ads) > limit:
        env.claim("hiring_activity", "job_postings_total", len(ads), feed_ev,
                  note="active ads with the company's or its workplaces' organisation number; the %d most recent are "
                       "listed" % limit)
    if found == 0:
        if index.status == "loaded":
            gap = (" (%d active ads could not be read)" % index.missing) if index.missing else ""
            n = len(own) - 1
            units = ("" if not n else " or that of its registered workplace" if n == 1 else
                     " or that of any of its %d registered workplaces" % n)
            env.claim("hiring_activity", "job_postings", None, feed_ev, "not_available",
                      note="no active ad with this organisation number%s among %d active ads on Nav's official feed "
                           "(changes of the last %d days)%s" % (units, index.read, index.days, gap))
        else:
            env.claim("hiring_activity", "job_postings", None, feed_ev, "failed",
                      note="Nav's feed was read only in part (%d of %d active ads%s), so absence is not established" %
                           (index.read, index.listed, "; time limit reached" if index.out_of_time else
                            "; page limit reached" if index.pages >= index.max_pages else ""))
    return {"jobs": found, "homepages": homepages}
