"""Company-owned layer: find the official website, prove it belongs to the exact legal entity, extract dated facts.

Identity rule: a site is published as the company's own only when its pages show the organisation number, or when
the company itself listed it in the register and the site stays on that domain and names the company. Domains guessed
from the name need the organisation number on the page. Everything else is "ambiguous" or "not_available".
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from .http import USER_AGENT
from .model import Envelope

LEGAL_SUFFIX = re.compile(r"\b(AS|ASA|ANS|DA|SA|BA|NUF|ENK|IKS|KF|SF|STI|FLI|BRL|ESEK|SAM|KS|PRE)\b\.?$", re.I)
SOCIAL = {
    "linkedin": re.compile(r"^https?://([a-z]{2,3}\.)?linkedin\.com/(company|school|showcase)/[^/?#]+", re.I),
    "facebook": re.compile(r"^https?://(www\.|m\.|nb-no\.)?facebook\.com/(?!sharer|share|dialog|plugins|tr\b|"
                           r"login|policy|privacy|help|groups/?$)[^?#]{2,}", re.I),
    "instagram": re.compile(r"^https?://(www\.)?instagram\.com/(?!p/|explore|accounts|share)[^/?#]{2,}", re.I),
    "x": re.compile(r"^https?://(www\.)?(twitter|x)\.com/(?!intent|share|home|search|i/)[^/?#]{2,}", re.I),
    "youtube": re.compile(r"^https?://(www\.)?youtube\.com/(channel/|c/|user/|@)[^/?#]+", re.I),
    "tiktok": re.compile(r"^https?://(www\.)?tiktok\.com/@[^/?#]+", re.I),
}
PAGE_HINTS = {
    "contact": re.compile(r"(kontakt|contact|om-oss|om_oss|omoss|about|personvern|privacy|vilkar|vilk%C3%A5r|terms|"
                          r"impressum|selskapet|company)", re.I),
    "careers": re.compile(r"(karriere|jobb|ledige|stillinger|careers?|\bjobs?\b|work-with-us)", re.I),
    "news": re.compile(r"(nyhet|aktuelt|\bnews\b|blogg|\bblog\b|presse|artikler|articles)", re.I),
}
GENERIC_MAIL = re.compile(r"^(post|postmottak|firmapost|info|kontakt|contact|salg|sales|office|kontor|mail|hello|"
                          r"hei|support|kundeservice|service|booking|bestilling|order|ordre|faktura|invoice|regnskap|"
                          r"admin|resepsjon|sentralbord)@", re.I)
DATE_RE = re.compile(r"\b(20[0-3]\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")


def orgnr_patterns(orgnr: str) -> re.Pattern:
    d = orgnr
    sep = r"[\s \.\-]?"
    return re.compile(r"(?<!\d)%s%s%s%s%s(?!\d)" % (d[:3], sep, d[3:6], sep, d[6:]))


def fold(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def name_core(legal_name: str) -> str:
    n = LEGAL_SUFFIX.sub("", legal_name.strip()).strip(" ,.-")
    return n


def name_tokens(legal_name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", fold(name_core(legal_name))) if len(t) >= 3]


def guess_domains(legal_name: str) -> list[str]:
    core = fold(name_core(legal_name)).replace("&", " og ").replace("+", " ")
    words = [w for w in re.split(r"[^a-z0-9]+", core) if w]
    if not words or sum(len(w) for w in words) < 4 or len(words) > 4:
        return []
    joined, hyph = "".join(words), "-".join(words)
    out = [joined + ".no"]
    if len(words) > 1:
        out.append(hyph + ".no")
    out.append(joined + ".com")
    return out


def normalize_site(url: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlsplit(url)
    if not p.hostname or "." not in p.hostname:
        return None
    return urlunsplit((p.scheme.lower(), p.hostname.lower(), "/", "", ""))


def site_key(host: str | None) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


class Page(HTMLParser):
    """Links, title, meta tags, JSON-LD blocks, feeds and visible text of one HTML page."""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self.jsonld: list = []
        self.feeds: list[str] = []
        self.times: list[str] = []
        self._text: list[str] = []
        self._stack: list[str] = []
        self._href = None
        self._atext: list[str] = []
        self._script = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript", "template"):
            self._script = (tag, (a.get("type") or "").lower(), [])
        elif tag == "a" and a.get("href"):
            self._href = urljoin(self.base, a["href"].strip())
            self._atext = []
        elif tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k and a.get("content"):
                self.meta[k] = a["content"].strip()
        elif tag == "link" and "alternate" in (a.get("rel") or "").lower():
            if (a.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml") and a.get("href"):
                self.feeds.append(urljoin(self.base, a["href"]))
        elif tag == "time" and a.get("datetime"):
            self.times.append(a["datetime"])
        self._stack.append(tag)

    def handle_endtag(self, tag):
        if self._script and tag == self._script[0]:
            _, typ, buf = self._script
            if typ == "application/ld+json":
                try:
                    self.jsonld.append(json.loads("".join(buf)))
                except ValueError:
                    pass
            self._script = None
        elif tag == "a" and self._href:
            self.links.append((self._href, " ".join("".join(self._atext).split())[:120]))
            self._href = None
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if self._script:
            self._script[2].append(data)
            return
        if self._stack and self._stack[-1] == "title":
            self.title += data
        if self._href is not None:
            self._atext.append(data)
        self._text.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self._text).split())


def parse_page(resp) -> Page | None:
    ctype = (resp.headers.get("content-type") or "").lower()
    if not resp.ok or ("html" not in ctype and not resp.body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))):
        return None
    p = Page(resp.final_url or resp.url)
    try:
        p.feed(resp.text())
        p.close()
    except Exception:
        pass
    return p


def jsonld_items(blocks) -> list[dict]:
    out = []

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            if "@graph" in o:
                walk(o["@graph"])
            if o.get("@type"):
                out.append(o)
    walk(blocks)
    return out


def types_of(item: dict) -> set[str]:
    t = item.get("@type")
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


class Site:
    """Fetches pages of one host under robots.txt, at most `budget` pages."""

    def __init__(self, client, env: Envelope, root: str, budget: int = 8):
        self.client, self.env, self.root, self.budget = client, env, root, budget
        self.robots = None
        self.pages: dict[str, tuple] = {}

    deadline = None                              # time.monotonic() value after which no page is fetched

    def _robots(self):
        if self.robots is None:
            self.robots = urllib.robotparser.RobotFileParser()
            r = self.client.get(urljoin(self.root, "/robots.txt"), accept="text/plain", timeout=8, retries=0)
            self.robots_response = r
            if r.status == 0:
                self.dead = r.error or "no answer"
                self.robots.parse([])
            elif r.ok:
                self.robots.parse(r.text().splitlines())
            else:
                self.robots.parse([])            # missing robots.txt: everything allowed
        return self.robots

    def get(self, url: str):
        if url in self.pages:
            return self.pages[url]
        if len(self.pages) >= self.budget:
            return None, None, None
        if self.deadline is not None and time.monotonic() > self.deadline:
            return None, None, "time budget for this company used up"
        rp = self._robots()
        if getattr(self, "dead", None):
            r = self.robots_response
            snap = self.env.snapshot(r, "company_owned", "robots_txt")
            self.pages[url] = (r, snap, None)    # the failed answer is the evidence that the host was checked
            return self.pages[url]
        if not rp.can_fetch(USER_AGENT, url):
            self.pages[url] = (None, None, "robots.txt disallows")
            return self.pages[url]
        r = self.client.get(url, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5",
                            timeout=12, retries=1)
        snap = self.env.snapshot(r, "company_owned", "html")
        self.pages[url] = (r, snap, None)
        return self.pages[url]


DECLARED = {"registry_listed": "listed by the company in the register",
            "nav_employer_homepage": "given as employer homepage in the company's own job ad on Nav"}


def find_website(client, env: Envelope, entity: dict, seconds: float = 75.0, extra: list | None = None) -> dict:
    """Return {'status', 'site', 'proof', ...}; adds website claims. `seconds` bounds the whole website stage.

    `extra`: further declared candidates as (url, origin), e.g. the employer homepage of a matched Nav job ad."""
    deadline = time.monotonic() + seconds
    name = entity.get("navn") or ""
    pat = orgnr_patterns(env.orgnr)
    registry = normalize_site(entity.get("hjemmeside") or "")
    candidates, seen = [], set()

    def add(u, origin):
        u = normalize_site(u or "")
        if u and site_key(urlsplit(u).hostname) not in seen:
            seen.add(site_key(urlsplit(u).hostname))
            candidates.append((u, origin))
    if registry:
        add(registry, "registry_listed")
    for u, origin in extra or []:
        add(u, origin)
    for d in guess_domains(name):
        add("https://" + d + "/", "name_guess")
    tokens = name_tokens(name)
    for url, origin in candidates:
        site = Site(client, env, url)
        site.deadline = deadline
        r, snap, why = site.get(url)
        if r is None or not r.ok:
            if origin == "registry_listed":
                state = "blocked" if why else ("not_available" if r is not None and r.status in (404, 410) or
                                               (r is not None and r.status == 0) else "failed")
                ev = [env.cite(snap, "HTTP %s %s" % (r.status, r.error))] if snap else []
                env.claim("website_profiles", "official_website", None, ev, state,
                          note="registry-listed website did not answer: %s" % (why or r.error or r.status))
            continue
        page = parse_page(r)
        if page is None:
            if origin == "registry_listed":
                env.claim("website_profiles", "official_website", None, [env.cite(snap, "not an HTML page")], "failed",
                          note="registry-listed website returned no readable HTML page")
            continue
        final_host = site_key(urlsplit(r.final_url).hostname)
        same_domain = final_host == site_key(urlsplit(url).hostname)
        proof_snap, proof_span = None, None
        m = pat.search(page.text)
        if m:
            proof_snap, proof_span = snap, page.text[max(0, m.start() - 60): m.end() + 20]
        else:
            for link, text in page.links:
                lp = urlsplit(link)
                if site_key(lp.hostname) != final_host or not PAGE_HINTS["contact"].search(lp.path + " " + text):
                    continue
                r2, snap2, _ = site.get(urlunsplit((lp.scheme, lp.netloc, lp.path, lp.query, "")))
                if r2 is not None and r2.ok:
                    p2 = parse_page(r2)
                    m2 = pat.search(p2.text) if p2 else None
                    if m2:
                        proof_snap, proof_span = snap2, p2.text[max(0, m2.start() - 60): m2.end() + 20]
                        break
                if len(site.pages) >= 4:
                    break
        named = bool(tokens) and all(t in fold(page.title + " " + page.text[:20000]) for t in tokens[:3])
        if proof_snap:
            proof, conf, state = "organisation number shown on the site", 0.99, "available"
        elif origin in DECLARED and same_domain and named:
            proof, conf, state = DECLARED[origin] + "; site names the company", 0.85, "available"
        elif origin in DECLARED:
            proof, conf, state = ("%s, but the site %s and shows no organisation number" % (DECLARED[origin],
                                  "redirects to %s" % final_host if not same_domain else "does not name the company"),
                                  0.4, "ambiguous")
            if origin != "registry_listed":
                continue                               # only the register's own entry is reported as ambiguous
        else:
            continue                                   # a guessed domain without the number is never published
        ev = [env.cite(snap, (page.title or "")[:160], "homepage")]
        if proof_snap:
            ev.append(env.cite(proof_snap, proof_span, "organisation_number"))
        env.claim("website_profiles", "official_website", r.final_url if state == "available" else None, ev, state,
                  confidence=conf, note=proof + ("; found by name-based domain check" if origin == "name_guess" else ""))
        if state != "available":
            return {"status": state}
        return {"status": "available", "site": site, "home": (r, snap, page), "origin": origin}
    if not any(c["field"] == "official_website" for c in env.claims):
        env.claim("website_profiles", "official_website", None, [], "not_available",
                  note="no website in the register; name-based domains (%s) showed no matching organisation number"
                  % ", ".join(d for d in guess_domains(name)) if guess_domains(name) else
                  "no website in the register and no usable name for a domain check")
    return {"status": "not_available"}


def extract_site(client, env: Envelope, found: dict) -> dict:
    """Facts from the verified site: description, own profiles, generic contact, careers, dated news, JSON-LD."""
    site: Site = found["site"]
    site.deadline = max(site.deadline or 0.0, time.monotonic() + 40.0)
    r, snap, page = found["home"]
    host = site_key(urlsplit(r.final_url).hostname)
    sec = "website_profiles"
    desc = page.meta.get("og:description") or page.meta.get("description")
    if desc:
        env.claim(sec, "website_description", desc[:500], [env.cite(snap, desc[:300], "meta description")],
                  confidence=0.9, note="company-owned description, not independent evidence")
    if page.title.strip():
        env.claim(sec, "website_title", page.title.strip()[:200], [env.cite(snap, page.title.strip()[:200], "title")],
                  confidence=0.9)
    profiles = {}
    items = jsonld_items(page.jsonld)
    for it in items:
        same = it.get("sameAs")
        for s in (same if isinstance(same, list) else [same]):
            if isinstance(s, str):
                for plat, rx in SOCIAL.items():
                    if rx.match(s) and plat not in profiles:
                        profiles[plat] = (s, "JSON-LD sameAs")
    marks = set(name_tokens(env.legal_name or "")) | {fold(host.split(".")[0])}
    for link, text in page.links:
        for plat, rx in SOCIAL.items():
            if plat not in profiles and rx.match(link):
                slug = fold(urlsplit(link).path)
                if any(m and (m in slug or slug.strip("/@") in m) for m in marks if len(m) >= 3):
                    profiles[plat] = (link.split("?")[0].rstrip("/"), "outbound link on the company site")
    for plat, (url, how) in sorted(profiles.items()):
        env.claim(sec, "company_profile", {"platform": plat, "url": url},
                  [env.cite(snap, "%s (%s)" % (url, how), "company_profile")], confidence=0.95,
                  note="linked from the verified company website")
    mails = sorted({l[7:].split("?")[0].lower() for l, _ in page.links if l.lower().startswith("mailto:")})
    for m in [m for m in mails if GENERIC_MAIL.match(m)][:3]:
        env.claim(sec, "company_email", m, [env.cite(snap, "mailto:" + m, "contact")], confidence=0.9)
    for it in items:
        t = types_of(it)
        if t & {"organization", "localbusiness", "corporation", "store", "restaurant", "professionalservice"}:
            addr = it.get("address")
            addrs = addr if isinstance(addr, list) else [addr]
            for a in addrs:
                if isinstance(a, dict) and (a.get("streetAddress") or a.get("addressLocality")):
                    txt = ", ".join(str(a.get(k)) for k in ("streetAddress", "postalCode", "addressLocality")
                                    if a.get(k))
                    env.claim("leadership_workplaces", "website_location", txt,
                              [env.cite(snap, txt, "JSON-LD address")], confidence=0.85,
                              note="address published on the company website")
    hiring = activity = 0
    for kind in ("careers", "news"):
        for link, text in page.links:
            lp = urlsplit(link)
            if site_key(lp.hostname) == host and PAGE_HINTS[kind].search(lp.path + " " + text) and lp.path not in ("",
                                                                                                                   "/"):
                url = urlunsplit((lp.scheme, lp.netloc, lp.path, "", ""))
                rr, ss, why = site.get(url)
                if rr is None or not rr.ok:
                    break
                pg = parse_page(rr)
                if kind == "careers":
                    env.claim("hiring_activity", "careers_page", rr.final_url,
                              [env.cite(ss, (pg.title if pg else text)[:160], "careers page")], confidence=0.9)
                    hiring += 1
                    for it in jsonld_items(pg.jsonld) if pg else []:
                        if "jobposting" in types_of(it) and it.get("title"):
                            env.claim("hiring_activity", "job_posting",
                                      {"title": str(it.get("title"))[:200], "date_posted": it.get("datePosted"),
                                       "valid_through": it.get("validThrough"), "source": "company website"},
                                      [env.cite(ss, str(it.get("title"))[:200], "JSON-LD JobPosting")],
                                      effective_date=(it.get("datePosted") or "")[:10] or None)
                            hiring += 1
                else:
                    activity += news_items(env, ss, pg, rr.final_url)
                break
    for feed in (page.feeds or [])[:1]:
        rr, ss, why = site.get(feed)
        if rr is not None and rr.ok:
            activity += feed_items(env, ss, rr.text())
    return {"profiles": len(profiles), "hiring": hiring, "activity": activity}


PLACEHOLDER_TITLE = re.compile(r"^(hello world|hei verden|sample page|eksempelside|uncategorized)\W*$", re.I)


def useful_item(title: str, date: str) -> bool:
    """Dated activity worth publishing: a real title and a date within the last three years, not in the future."""
    from datetime import date as _date, timedelta
    if not title or PLACEHOLDER_TITLE.match(title.strip()):
        return False
    try:
        d = _date.fromisoformat(date[:10])
    except ValueError:
        return False
    today = _date.today()
    return today - timedelta(days=3 * 366) <= d <= today + timedelta(days=7)


def news_items(env: Envelope, snap, page: Page | None, url: str, limit: int = 5) -> int:
    if page is None:
        return 0
    n = 0
    for it in jsonld_items(page.jsonld):
        if types_of(it) & {"newsarticle", "blogposting", "article", "event"} and (it.get("headline") or it.get("name")):
            d = (it.get("datePublished") or it.get("startDate") or "")[:10]
            if DATE_RE.match(d or "") and useful_item(str(it.get("headline") or it.get("name")), d):
                title = str(it.get("headline") or it.get("name"))[:200]
                env.claim("hiring_activity", "dated_activity", {"date": d, "title": title, "url": it.get("url") or url,
                                                                "source": "company website"},
                          [env.cite(snap, "%s %s" % (d, title), "JSON-LD article")], effective_date=d)
                n += 1
                if n >= limit:
                    return n
    return n


def feed_items(env: Envelope, snap, xml: str, limit: int = 5) -> int:
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    try:
        root = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return 0
    n = 0
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = date = None
        for ch in item:
            t = ch.tag.split("}")[-1]
            if t == "title":
                title = (ch.text or "").strip()
            elif t == "link":
                link = ch.get("href") or (ch.text or "").strip()
            elif t in ("pubDate", "published", "updated", "date") and not date:
                raw = (ch.text or "").strip()
                try:
                    date = parsedate_to_datetime(raw).date().isoformat()
                except (TypeError, ValueError):
                    m = DATE_RE.search(raw)
                    date = m.group(0) if m else None
        if title and date and useful_item(title, date):
            env.claim("hiring_activity", "dated_activity", {"date": date, "title": title[:200], "url": link,
                                                            "source": "company website feed"},
                      [env.cite(snap, "%s %s" % (date, title[:200]), "RSS/Atom item")], effective_date=date)
            n += 1
            if n >= limit:
                break
    return n
