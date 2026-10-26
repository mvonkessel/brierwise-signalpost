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
from .model import Envelope, state_of_status

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
ORG_CONTEXT = re.compile(r"(org|foretak|mva|vat|company|registr|reg\.|nr\.|enhetsreg)", re.I)
DATE_RE = re.compile(r"\b(20[0-3]\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")


def orgnr_patterns(orgnr: str) -> re.Pattern:
    """The nine digits with at most one space, dot or dash between any two of them («923 609 016», «976 76 5524»)."""
    sep = r"[\s\u00a0\.\-]?"
    return re.compile(r"(?<!\d)%s(?!\d)" % sep.join(orgnr))


def fold(s: str) -> str:
    s = s.lower().replace("æ", "ae").replace("ø", "o").replace("å", "a")
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def name_core(legal_name: str) -> str:
    n = LEGAL_SUFFIX.sub("", legal_name.strip()).strip(" ,.-")
    return n


# Words for the kind of body, not its name («Stiftelsen Midtnorsk Jazzsenter»): a site names a foundation or an
# association when it shows the other words. Housing co-ownerships keep theirs; their register sites are mostly the
# manager's.
FORM_WORDS = {"stiftelsen", "stiftelse", "stiftinga", "stifting", "foreningen", "forening"}
# Words for the kind of body that a domain usually leaves out («SAMEIET ST OLAV» on st-olav.no).
BODY_WORDS = FORM_WORDS | {"sameiet", "sameie", "boligsameiet", "eierseksjonssameiet", "borettslaget", "borettslag",
                           "andelslaget", "andelslag"}


def name_tokens(legal_name: str) -> list[str]:
    toks = [t for t in re.split(r"[^a-z0-9]+", fold(name_core(legal_name))) if len(t) >= 3]
    return [t for t in toks if t not in FORM_WORDS] or toks


def domain_is_name(host: str, legal_name: str) -> bool:
    """The domain (no subdomain) spells the whole name without legal form: «CSB INVEST AS» on csbinvest.net."""
    labels = site_key(host).split(".")
    if len(labels) != 2:
        return False
    raw = name_core(legal_name).lower().replace("&", "og")
    names = {re.sub(r"[^a-z0-9]", "", fold(f)) for f in (raw, raw.replace("å", "aa").replace("ø", "oe"))}
    return len(labels[0]) >= 5 and labels[0].replace("-", "") in names


# Placeholder pages: a parked, for-sale, unfinished or switched-off site says nothing about who runs it.
PLACEHOLDER = re.compile(r"(domain (name )?(is|may be) for sale|buy this domain|domenet (er )?til salgs|kjop dette "
                         r"domenet|parkert (hos|av)|parked (free|domain|by)|under construction|under oppbygging|"
                         r"nettsiden er (for oyeblikket |midlertidig )?utilgjengelig|site (is )?(temporarily )?"
                         r"unavailable|account (has been )?suspended|default web site page|welcome to nginx|"
                         r"it works!|index of /)")


def placeholder(page) -> bool:
    return bool(PLACEHOLDER.search(fold(page.title + " " + page.text[:3000])))


GENERIC_WORDS = {"holding", "holdings", "invest", "investering", "investment", "investments", "eiendom", "eiendommer",
                 "eigedom", "properties", "property", "consulting", "consult", "konsult", "group", "gruppen", "gruppe",
                 "norge", "norway", "norden", "nordic", "service", "services", "tjenester", "drift", "utvikling",
                 "entreprenor", "bygg", "transport", "regnskap", "management", "partner", "partners", "og", "and", "co",
                 "company", "selskap", "selskapet", "firma"}
# First words that name a place, a quality or a trade rather than a company: no first-word guess for these.
COMMON_FIRST = {"norske", "norsk", "norges", "nordisk", "nordiske", "nord", "vest", "sor", "ost", "midt", "store",
                "lille", "gamle", "nye", "silver", "gold", "golden", "green", "blue", "black", "white", "first", "best",
                "super", "smart", "total", "global", "inter", "euro", "scan", "data", "tech", "auto", "huset", "oslo",
                "bergen", "trondheim", "stavanger", "tromso", "drammen", "kristiansand", "fredrikstad", "sandnes",
                "baerum", "asker", "bodo", "alesund", "haugesund", "tonsberg", "moss", "hamar", "larvik", "halden",
                "molde", "harstad", "gjovik", "lillehammer", "horten", "arendal", "porsgrunn", "skien", "kongsberg",
                "holmestrand", "sarpsborg", "steinkjer", "narvik", "alta", "kirkenes", "voss", "stord", "agder",
                "rogaland", "vestland", "viken", "innlandet", "telemark", "nordland", "troms", "finnmark", "trondelag"}


def guess_domains(legal_name: str) -> list[tuple[str, str]]:
    """Candidate domains from the legal name as (domain, kind). «full» uses the whole name (also with «aa»/«oe» for
    å/ø and as an internationalised domain); «short» drops generic words or keeps only the first word. A short guess is
    published only with the organisation number on the site, because a group or a namesake can own it."""
    raw = name_core(legal_name).lower().replace("&", " og ").replace("+", " ")
    out: list[tuple[str, str]] = []

    def add(d, kind):
        if d and d not in [x for x, _ in out]:
            out.append((d, kind))
    forms = [fold(raw)]
    if re.search("[åø]", raw):
        forms.append(fold(raw.replace("å", "aa").replace("ø", "oe")))
    shorts = []
    for f in forms:
        for words in name_variants(f):
            if not words or sum(len(w) for w in words) < 4 or len(words) > 4:
                continue
            add("".join(words) + ".no", "full")
            if len(words) > 1:
                add("-".join(words) + ".no", "full")
            core = [w for w in words if w not in GENERIC_WORDS]
            if core and core != words and sum(len(w) for w in core) >= 4:
                shorts.append("".join(core) + ".no")
            if (len(words) > 1 and len(words[0]) >= 4 and
                    words[0] not in GENERIC_WORDS | COMMON_FIRST | BODY_WORDS):
                shorts.append(words[0] + ".no")
    if out and re.search("[æøå]", raw):
        words = [w for w in re.split(r"[^a-z0-9æøå]+", raw) if w]
        try:
            add("".join(words).encode("idna").decode("ascii") + ".no", "full")
        except UnicodeError:
            pass
    if out:
        add(out[0][0][:-3] + ".com", "full")
    for d in shorts:
        add(d, "short")
    return out[:10]


def name_variants(folded: str) -> list[list[str]]:
    """Word lists a domain may be built from: the name as written, with initials joined («P.E. GAARUD» → pe gaarud),
    and without a leading word for the kind of body («SAMEIET ST OLAV» → st olav)."""
    words = [w for w in re.split(r"[^a-z0-9]+", folded) if w]
    merged, buf = [], ""
    for w in words:
        if len(w) == 1 and w.isalpha():
            buf += w
            continue
        if buf:
            merged.append(buf)
            buf = ""
        merged.append(w)
    if buf:
        merged.append(buf)
    out = [words]
    for v in (merged, words[1:] if len(words) > 1 and words[0] in BODY_WORDS else None):
        if v and v not in out:
            out.append(v)
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


BRAVE = "https://api.search.brave.com/res/v1/web/search"
SEARCH_COST_USD = 0.005          # declared estimate per query; the key and its budget come from the evaluator
NOT_OWN = re.compile(r"(^|\.)(proff|purehelp|1881|gulesider|brreg|finn|linkedin|facebook|instagram|youtube|x|twitter|"
                     r"wikipedia|wikidata|allabolag|bisnode|dnb|regnskapstall|forvalt|kompass|enirogroup|eniro|"
                     r"nav|arbeidsplassen|kununu|glassdoor|indeed|tiktok|google|bing|yelp|tripadvisor|hotels|"
                     r"booking|mittanbud|anbud365|firmasok|bedriftsdatabasen|ratsit|merinfo|largestcompanies)\.",
                     re.I)


def search_candidates(client, env: Envelope, entity: dict, count: int = 6) -> list[str]:
    """Optional: root URLs of web-search results for the company name. Only used when the evaluator supplies
    BRAVE_SEARCH_API_KEY. Results are candidates, never evidence: a site is published only after the organisation number
    and the company name are found on the site itself."""
    import os
    from urllib.parse import quote_plus
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    core = name_core(entity.get("navn") or "")
    if not key or len(core) < 3:
        return []
    town = ((entity.get("forretningsadresse") or {}).get("poststed") or "").title()
    q = '"%s" %s' % (core, town)
    r = client.get(BRAVE + "?q=%s&count=%d&country=NO" % (quote_plus(q), count), accept="application/json",
                   headers={"X-Subscription-Token": key}, timeout=15, retries=1)
    env.cost_usd = getattr(env, "cost_usd", 0.0) + SEARCH_COST_USD
    if not r.ok:
        env.error("website.search", "search API answered %s" % r.status)
        return []
    out = []
    try:
        results = ((r.json().get("web") or {}).get("results")) or []
    except ValueError:
        return []
    for res in results:
        host = urlsplit(res.get("url") or "").hostname or ""
        if host and not NOT_OWN.search(site_key(host)) and host not in out:
            out.append("https://%s/" % host)
    return out[:4]


DECLARED = {"registry_listed": "listed by the company in the register",
            "nav_employer_homepage": "given as employer homepage in the company's own job ad on Nav",
            "subunit_listed": "listed in the register for a registered workplace of the company"}
ORIGIN_NOTE = {"name_full": "; found by a domain check of the legal name",
               "name_short": "; found by a domain check of the shortened legal name",
               "email_domain": "; candidate from the domain of the company's registered e-mail address",
               "search_candidate": "; candidate from a web search, verified on the site itself",
               "previous_run": "; the site verified in the previous run, checked again"}
# Mail and internet providers: the domain of a registered address there says nothing about the company's own site.
FREE_MAIL = {"gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.no",
             "live.com", "msn.com", "yahoo.com", "yahoo.no", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com",
             "gmx.com", "gmx.net", "gmx.de", "protonmail.com", "proton.me", "pm.me", "online.no", "frisurf.no",
             "broadpark.no", "getmail.no", "start.no", "c2i.net", "lyse.net", "altibox.no", "telenor.no", "epost.no",
             "mail.com", "email.com", "tele2.no", "chello.no", "bluezone.no", "haugnett.no", "enivest.net",
             "nextgentel.com", "ebnett.no", "vikenfiber.no", "fiberby.no", "networks.no", "sensewave.com", "tdc.no",
             "netcom.no", "globalconnect.no", "hotmail.co.uk", "yahoo.co.uk", "mail.ru", "yandex.ru", "qq.com"}
STREET_NUM = re.compile(r"^(?P<street>[a-z][a-z .'-]{1,}?)\.?\s+(?P<num>\d{1,4})\s?(?P<letter>[a-z]?)\b")


def fold_map(text: str) -> tuple[str, list[int]]:
    """Folded text and, for every folded character, the index of the original character it came from."""
    out, idx = [], []
    for i, ch in enumerate(text):
        for c in (fold(ch) if not ch.isascii() else ch.lower()):
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def quote(text: str, start: int, end: int, before: int = 60, after: int = 30) -> str:
    """Exact quote around text[start:end] that stops short of e-mail addresses and phone numbers, so the span can be
    found verbatim in the source without republishing people's contact details."""
    from .model import EMAIL, PHONE
    a, b = max(0, start - before), min(len(text), end + after)
    for rx in (EMAIL, PHONE):
        for m in rx.finditer(text, max(0, a - 40), min(len(text), b + 40)):
            if m.end() <= start:
                a = max(a, m.end())
            elif m.start() >= end:
                b = min(b, m.start())
    return text[a:b].strip()


def address_keys(entity: dict) -> list[tuple]:
    """(street pattern, postcode) for the registered business and postal address; post boxes are skipped."""
    keys = []
    for k in ("forretningsadresse", "postadresse"):
        a = entity.get(k) or {}
        pn = a.get("postnummer")
        for line in a.get("adresse") or []:
            fl = " ".join(fold(line or "").split())
            if not fl or not pn or fl.startswith(("postboks", "pb ", "pb.", "boks ", "c/o", "postb")):
                continue
            m = STREET_NUM.match(fl)
            if m and len(m.group("street").strip()) >= 2:    # short names exist («Sæ 20»); postcode and name still needed
                letter = m.group("letter")
                rx = re.compile(r"(?<![a-z])%s\.?\s*%s%s(?![0-9a-z])" % (
                    re.escape(m.group("street").strip()), m.group("num"), r"\s?" + letter if letter else ""))
                keys.append((rx, pn))
    return keys


ANY_ORGNR = re.compile(r"(?<!\d)([89]\d{2})[\s \.\-]?(\d{3})[\s \.\-]?(\d{3})(?!\d)")


def other_orgnrs(text: str, own: str) -> int:
    """Distinct valid organisation numbers other than `own` shown in a register context: a page listing several
    companies (group overview, directory, accountant's client list) proves nobody's identity."""
    from .model import valid_orgnr
    found = set()
    for m in ANY_ORGNR.finditer(text):
        n = "".join(m.groups())
        if n != own and valid_orgnr(n) and ORG_CONTEXT.search(text[max(0, m.start() - 40): m.start()]):
            found.add(n)
    return len(found)


def scan_page(text: str, pat: re.Pattern, keys: list) -> tuple:
    """Best identity evidence on one page: ('orgnr', span), ('orgnr_shared', span), ('address', span) or (None, None).
    'orgnr_shared': the number is there, but next to other companies' numbers."""
    for m in pat.finditer(text):
        span = quote(text, m.start(), m.end())
        if ORG_CONTEXT.search(span):
            own = re.sub(r"\D", "", m.group(0))
            return ("orgnr_shared" if other_orgnrs(text, own) else "orgnr"), span
    if keys:
        folded, idx = fold_map(text)
        for rx, pn in keys:
            hm = rx.search(folded)
            if hm and re.search(r"(?<!\d)%s(?!\d)" % pn, folded):
                return "address", quote(text, idx[hm.start()], idx[hm.end() - 1] + 1, 0, 25)
    return None, None


def profile_url(url: str) -> str:
    """Canonical profile address: without query and fragment, except Facebook's numeric «profile.php?id=…»."""
    p = urlsplit(url)
    m = re.match(r"^/(company|school|showcase)/([^/?#]+)", p.path)
    if m and (p.hostname or "").endswith("linkedin.com"):
        return urlunsplit((p.scheme or "https", p.netloc, "/%s/%s" % (m.group(1), m.group(2)), "", ""))
    if p.path.rstrip("/").endswith("profile.php"):
        q = "&".join(x for x in p.query.split("&") if x.startswith("id="))
        return urlunsplit((p.scheme, p.netloc, p.path, q, ""))
    return url.split("?")[0].split("#")[0].rstrip("/")


def handle_matches(slug: str, marks: set) -> bool:
    """Does a profile path belong to the company? A name word or the site's domain label of four letters or more must
    appear in it (or the account name in that word); a three-letter word only as the whole account name, optionally
    with a legal form, country word or number («tgs», «tgs_220114»), never as the start of a person's name
    («KaiHenningMelberg» for «KAI MARKETING AS»)."""
    handle = re.sub(r"[-_.]", "", slug.strip("/@").split("/")[-1])
    for m in marks:
        if not m:
            continue
        if len(m) >= 4 and (m in slug or slug.strip("/@") in m):
            return True
        if len(m) == 3 and re.fullmatch(r"%s(as|asa|no|norge|norway|group|official)?\d*" % re.escape(m), handle):
            return True
    return False


def self_names(page: Page) -> list[str]:
    """Names a site gives itself: og:site_name and JSON-LD Organization names."""
    names = [page.meta.get("og:site_name") or ""]
    names += [it["name"] for it in jsonld_items(page.jsonld)
              if types_of(it) & {"organization", "corporation", "localbusiness"} and isinstance(it.get("name"), str)]
    return [n.strip() for n in names if n and n.strip()]


def other_entity(page: Page, legal_name: str) -> str | None:
    """A self-name with a legal form («Nortrans AS») that is not this company's name: the site belongs to, or is shared
    with, another legal entity (parent, sister, brand company), so name and address alone prove nothing."""
    own = fold(name_core(legal_name))
    for n in self_names(page):
        if LEGAL_SUFFIX.search(n) and fold(name_core(n)) != own:
            return n
    return None


DNS_FAIL = re.compile(r"getaddrinfo|name or service not known|nodename nor servname|no address associated|"
                      r"name does not resolve|host is not public", re.I)


LEADER_ROLES = {"DAGL": "daily manager", "LEDE": "chair of the board"}
# Origins where a name and a registered leader on the site tie it to the company; a shortened-name guess or a workplace
# site may belong to a sister company or a chain with the same people.
LEADER_ORIGINS = {"registry_listed", "nav_employer_homepage", "email_domain", "name_full", "search_candidate",
                  "previous_run"}


def leaders_of(env: Envelope) -> list[tuple[str, str]]:
    """(full name, role code) of the registered daily manager and chair, from this envelope's role claims."""
    out = []
    for c in env.claims:
        v = c.get("value")
        if c.get("field") == "role" and isinstance(v, dict) and v.get("kind") == "person" and \
                v.get("role_code") in LEADER_ROLES and v.get("name"):
            out.append((v["name"], v["role_code"]))
    return out


def leader_on_page(text: str, leaders: list[tuple[str, str]]):
    """(role code, exact quote) of the first leader whose full name the page shows («Ola Nordmann», also without a
    middle name), or None."""
    folded, idx = fold_map(text)
    for name, code in leaders:
        parts = [p for p in re.split(r"[^a-z0-9]+", fold(name)) if p]
        if len(parts) < 2 or min(len(parts[0]), len(parts[-1])) < 2:
            continue
        for seq in ([parts, [parts[0], parts[-1]]] if len(parts) > 2 else [parts]):
            m = re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % r"[\s\-]+".join(map(re.escape, seq)), folded)
            if m:
                return code, quote(text, idx[m.start()], idx[m.end() - 1] + 1, 50, 30)
    return None


def titled_entity(page: Page, text: str, legal_name: str) -> str | None:
    """A name from the page title or og:site_name that the page also prints with a legal form, other than this company
    («Wenaas Eftf» in the title and «Wenaas EFTF. AS» in the footer of wenaaskapital.no for WENAAS KAPITAL AS): the
    site presents another legal entity, typically a sister or parent company at the same address."""
    own = re.sub(r"[^a-z0-9]+", " ", fold(name_core(legal_name))).strip()
    folded = re.sub(r"[^a-z0-9]+", " ", fold(text))
    brands = re.split(r"\s[|\u2013\u2014:-]\s", page.title or "") + [page.meta.get("og:site_name") or ""]
    for b in brands:
        b = re.sub(r"[^a-z0-9]+", " ", fold(LEGAL_SUFFIX.sub("", b.strip()))).strip()
        if len(b) < 3 or b == own:
            continue
        if re.search(r"(?<![a-z0-9])%s (as|asa|ans|da|sa|ba)(?![a-z0-9])" % re.escape(b), folded):
            return b
    return None


def without_leaders(folded: str, leaders: list[tuple[str, str]]) -> str:
    """Folded text with the leaders' names blanked out: the company name must appear on its own, not only inside a
    person's name («STEINER BYGG AS» and its daily manager «Matthias Steiner Nilsen»)."""
    for name, _ in leaders:
        parts = [p for p in re.split(r"[^a-z0-9]+", fold(name)) if p]
        for seq in ([parts, [parts[0], parts[-1]]] if len(parts) > 2 else [parts]):
            if len(seq) >= 2:
                folded = re.sub(r"(?<![a-z0-9])%s(?![a-z0-9])" % r"[\s\-]+".join(map(re.escape, seq)), " ", folded)
    return folded


def fetch_state(r, why) -> str:
    """State of a candidate that gave no usable page. Our own limits and transient network errors (timeouts, TLS
    handshakes, resets) are `failed`; a domain that does not resolve or a missing page is `not_available`; a refusal by
    the site (robots.txt, 401/403/429/451) is `blocked`."""
    if why:
        return "blocked" if "robots" in why else "failed"
    if r is None:
        return "failed"
    if r.status == 0:
        return "not_available" if DNS_FAIL.search(r.error or "") else "failed"
    return state_of_status(r.status)


def find_website(client, env: Envelope, entity: dict, seconds: float = 75.0, extra: list | None = None,
                 subunits: list | None = None, previous_site: str | None = None) -> dict:
    """Return {'status', 'site', 'proof', ...}; adds website claims. `seconds` bounds the whole website stage.

    Candidates, in order: the register's website, homepages the company gave in its own Nav ads, the domain of its
    registered e-mail address, websites registered for its workplaces, domains built from the legal name, and (only
    with an evaluator key) web-search results. Publication needs one of:
    - the organisation number in a register context («org.nr», «MVA» ...) on the home or a contact page, and the
      company name unless the company itself declared the site;
    - the company name plus the registered street address with house number and postcode on the same domain (not for
      shortened-name guesses, where a group or a namesake may own the domain);
    - a site the company declared itself that stays on its domain and names the company;
    - the company name and the full name of its registered daily manager or chair of the board on the site (not for
      shortened-name guesses or workplace sites, and not when a page shows other companies' organisation numbers).
    Anything else stays unpublished; a register entry that fails these tests is reported as «ambiguous»."""
    deadline = time.monotonic() + seconds
    name = entity.get("navn") or ""
    pat = orgnr_patterns(env.orgnr)
    keys = address_keys(entity)
    registry = normalize_site(entity.get("hjemmeside") or "")
    register_snap = next((s for s in env.sources if s.get("extraction") == "brreg_enheter_json"), None)
    raw_hp = (entity.get("hjemmeside") or "").strip()
    hp_url = raw_hp if re.match(r"^https?://", raw_hp, re.I) else "https://" + raw_hp
    for plat, rx in SOCIAL.items():
        if raw_hp and register_snap and rx.match(hp_url):
            # The company gave a platform page as its website: a declared own profile; the platform is not fetched.
            env.claim("website_profiles", "company_profile",
                      {"platform": plat, "url": profile_url(hp_url)},
                      [env.cite(register_snap, "hjemmeside: %s" % raw_hp, "hjemmeside")], confidence=0.95,
                      note="given by the company as its website in the register; the platform page was not fetched")
            registry = None
            break
    candidates, seen = [], set()

    def add(u, origin):
        u = normalize_site(u or "")
        host = site_key(urlsplit(u).hostname) if u else ""
        if u and host not in seen and (origin in DECLARED or not NOT_OWN.search(host)):
            seen.add(host)
            candidates.append((u, origin))
    if registry:
        add(registry, "registry_listed")
    for u, origin in extra or []:
        add(u, origin)
    mail = entity.get("epostadresse") or ""
    mail_domain = mail.rsplit("@", 1)[-1].strip().lower() if "@" in mail else ""
    if mail_domain and mail_domain not in FREE_MAIL and re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", mail_domain):
        add("https://%s/" % mail_domain, "email_domain")
    for su in [s for s in subunits or [] if s.get("website")][:3]:
        add(su["website"], "subunit_listed")
    for d, kind in guess_domains(name):
        add("https://" + d + "/", "name_" + kind)
    if not registry:
        for u in search_candidates(client, env, entity):
            add(u, "search_candidate")
    # Refresh: the site verified in the previous run is checked again; when it cannot be read now (timeout, refused
    # connection, robots.txt), absence is not established and the website stays `failed`, not `not_available`.
    prev_url = normalize_site(previous_site or "")
    prev_host = site_key(urlsplit(prev_url).hostname) if prev_url else ""
    if prev_url:
        add(prev_url, "previous_run")
    tokens = name_tokens(name)
    leaders = leaders_of(env)
    deferred = None                                   # the register's own site when it failed the tests
    prev_failed = None                                # the previous run's site when it could not be read now
    for url, origin in candidates:
        if time.monotonic() > deadline:
            env.error("website", "time budget for the website search used up")
            if prev_host and not prev_failed:
                prev_failed = ("failed", [], "the website search ran out of time before the site verified in the "
                               "previous run (%s) was checked again" % prev_host, 0.9)
            break
        site = Site(client, env, url)
        site.deadline = deadline
        r, snap, why = site.get(url)
        if r is None or not r.ok:
            state = fetch_state(r, why)
            is_prev = site_key(urlsplit(url).hostname) == prev_host and state in ("failed", "blocked")
            if origin == "registry_listed" or is_prev:
                ev = [env.cite(snap, "HTTP %s %s" % (r.status, r.error))] if snap and r is not None else []
                reason = why or (r.error or "HTTP %s" % r.status if r is not None else "page budget used up")
                if origin == "registry_listed":
                    deferred = (state, ev, "registry-listed website did not answer: %s" % reason, 0.99)
                else:
                    prev_failed = (state, ev, "the website verified in the previous run (%s) could not be read in "
                                   "this run: %s" % (prev_host, reason), 0.9)
            continue
        page = parse_page(r)
        if page is None:
            if origin == "registry_listed":
                deferred = ("failed", [env.cite(snap, "not an HTML page")],
                            "registry-listed website returned no readable HTML page", 0.99)
            continue
        final_host = site_key(urlsplit(r.final_url).hostname)
        same_domain = final_host == site_key(urlsplit(url).hostname)
        if origin not in DECLARED and NOT_OWN.search(final_host):
            continue                                   # a redirect to a directory, marketplace or social platform
        named_text = fold(page.title + " " + page.text[:20000])
        home_text = named_text
        texts = [(snap, page.text)]
        kind, span = scan_page(page.text, pat, keys)
        proof_snap = snap if kind else None
        if kind != "orgnr":
            urls = []
            for l, t in page.links:
                lp = urlsplit(l)
                if (site_key(lp.hostname) == final_host and lp.path not in ("", "/") and
                        PAGE_HINTS["contact"].search(lp.path + " " + t)):
                    u2 = urlunsplit((lp.scheme, lp.netloc, lp.path, lp.query, ""))
                    if u2 not in urls:
                        urls.append(u2)
            if not urls:
                root = urlunsplit((urlsplit(r.final_url).scheme, urlsplit(r.final_url).netloc, "", "", ""))
                urls = [root + "/kontakt", root + "/om-oss"]
            for u2 in urls[:3]:
                r2, snap2, _ = site.get(u2)
                p2 = parse_page(r2) if r2 is not None and r2.ok else None
                if p2 is None:
                    continue
                named_text += " " + fold(p2.title + " " + p2.text[:20000])
                texts.append((snap2, p2.text))
                k2, s2 = scan_page(p2.text, pat, keys)
                if k2 == "orgnr" or (k2 and not kind) or (k2 == "orgnr_shared" and kind == "address"):
                    kind, span, proof_snap = k2, s2, snap2
                if kind == "orgnr":
                    break
        named = bool(tokens) and all(t in named_text for t in tokens[:3])
        named_home = bool(tokens) and all(t in home_text for t in tokens[:3])
        declared = origin in DECLARED
        other = other_entity(page, name)               # the site calls itself by another legal entity's name
        # A sole proprietorship often carries a person's name and sits at an employer's or agency's address: only its
        # organisation number on the site ties a site to it.
        weak_ok = ((entity.get("organisasjonsform") or {}).get("kode") or "") not in ("ENK",) and not other
        dummy = kind != "orgnr" and placeholder(page)
        lead = None
        if kind != "orgnr" and leaders and named and same_domain and weak_ok and origin in LEADER_ORIGINS and \
                not dummy and not any(other_orgnrs(t, env.orgnr) for _, t in texts) and \
                all(t in without_leaders(named_text, leaders) for t in tokens[:3]) and \
                not any(titled_entity(page, t, name) for _, t in texts):
            for s2, t2 in texts:
                hit = leader_on_page(t2, leaders)
                if hit:
                    lead = (hit[0], hit[1], s2)
                    break
        if kind == "orgnr" and (declared or named):
            proof, conf = "organisation number shown on the site", 0.99
        elif dummy:
            proof = None
        elif kind == "orgnr_shared" and declared and origin != "subunit_listed" and weak_ok:
            proof, conf = "organisation number shown on the site next to other companies' numbers", 0.9
        elif kind == "address" and named and same_domain and origin != "name_short" and weak_ok:
            proof, conf = "the site names the company and shows its registered street address", 0.9
        elif declared and origin != "subunit_listed" and same_domain and named_home and weak_ok:
            proof, conf = DECLARED[origin] + "; site names the company", 0.85
        elif origin == "registry_listed" and weak_ok and domain_is_name(final_host, name):
            proof, conf = DECLARED[origin] + ("; the domain spells the company name" if same_domain else
                                              "; it forwards to %s, which spells the company name" % final_host), 0.8
        elif origin == "email_domain" and same_domain and named_home and weak_ok:
            proof, conf = "the site names the company on its home page", 0.8
        elif lead:
            proof, conf = "the site names the company and its registered %s" % LEADER_ROLES[lead[0]], 0.85
            kind, span, proof_snap = "leader", lead[1], lead[2]
        else:
            proof = None
        if proof is None:
            if origin == "registry_listed":
                why_not = ("looks like a placeholder (parked, for sale or switched off)" if dummy else
                           "presents itself as «%s», another legal entity," % other if other else
                           "redirects to %s" % final_host if not same_domain else "does not name the company")
                deferred = ("ambiguous", [env.cite(snap, (page.title or "")[:160], "homepage")],
                            "%s, but the site %s and shows no organisation number" % (DECLARED[origin], why_not), 0.4)
            continue                                   # no proof: never published
        ev = [env.cite(snap, (page.title or "")[:160], "homepage")]
        if proof_snap is not None and span:
            ev.append(env.cite(proof_snap, span, {"address": "registered_address", "leader": "registered_leader"}.get(
                kind, "organisation_number")))
        if origin == "email_domain" and register_snap:
            ev.append(env.cite(register_snap, mail_domain, "epostadresse (domain only)"))
        if origin == "registry_listed" and register_snap:
            ev.append(env.cite(register_snap, "hjemmeside: %s" % raw_hp, "hjemmeside"))
        env.claim("website_profiles", "official_website", r.final_url, ev, "available", confidence=conf,
                  note=proof + ORIGIN_NOTE.get(origin, ""))
        return {"status": "available", "site": site, "home": (r, snap, page), "origin": origin}
    if prev_failed and not (deferred and deferred[0] in ("failed", "blocked")):
        deferred = prev_failed                        # absence is not established while that site cannot be read
    if deferred:
        state, ev, note, conf = deferred
        env.claim("website_profiles", "official_website", None, ev, state, confidence=conf, note=note)
        return {"status": state}
    tried = [urlsplit(u).hostname for u, o in candidates if o != "registry_listed"]
    env.claim("website_profiles", "official_website", None, [], "not_available",
              note=("no website in the register; checked %s without proof of identity" % ", ".join(tried[:10])
                    if tried else "no website in the register and no usable name for a domain check"))
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
    items = jsonld_items(page.jsonld)
    brand, how = page.meta.get("og:site_name"), "og:site_name"
    if not brand:
        for it in items:
            if types_of(it) & {"organization", "localbusiness", "corporation"} and isinstance(it.get("name"), str):
                brand, how = it["name"], "JSON-LD Organization name"
                break
    other = LEGAL_SUFFIX.search(brand or "") and fold(name_core(brand)) != fold(name_core(env.legal_name or ""))
    looks_like_domain = re.match(r"^(https?://)?(www\.)?[\w-]+(\.[a-z]{2,})+/?$", (brand or "").strip(), re.I)
    if brand and len(brand.strip()) >= 2 and fold(brand.strip()) not in GENERIC_BRAND and not other \
            and not looks_like_domain:
        env.claim("legal_identity", "public_brand", brand.strip()[:120], [env.cite(snap, brand.strip()[:120], how)],
                  confidence=0.9, note="name the verified company website uses for itself")
    profiles = {}                                     # account key -> (platform, url, link, how, snapshot)

    def keep(plat, link, how, s):
        u = profile_url(link)
        if u.startswith("http://"):
            u = "https://" + u[7:]
        p = urlsplit(u)
        key = "%s:%s?%s" % (plat, p.path.lower().rstrip("/"), p.query)   # same account whatever scheme, host or case
        if key not in profiles and sum(v[0] == plat for v in profiles.values()) < 2:
            profiles[key] = (plat, u, link, how, s)
    for it in items:
        same = it.get("sameAs")
        for s in (same if isinstance(same, list) else [same]):
            if isinstance(s, str):
                for plat, rx in SOCIAL.items():
                    if rx.match(s):
                        keep(plat, s, "JSON-LD sameAs", snap)
    marks = set(name_tokens(env.legal_name or "")) | {fold(host.split(".")[0])}
    pages = [(page, snap, "outbound link on the company site")]
    for link, text in page.links:
        lp = urlsplit(link)
        if site_key(lp.hostname) == host and lp.path not in ("", "/") and PAGE_HINTS["contact"].search(lp.path + " " +
                                                                                                         text):
            rr, ss, why = site.get(urlunsplit((lp.scheme, lp.netloc, lp.path, lp.query, "")))
            pg = parse_page(rr) if rr is not None and rr.ok else None
            if pg is not None:
                pages.append((pg, ss, "outbound link on a contact or about page of the company site"))
            break
    for pg, ss, how in pages:
        for link, text in pg.links:
            for plat, rx in SOCIAL.items():
                if rx.match(link):
                    if handle_matches(fold(urlsplit(profile_url(link)).path), marks):
                        keep(plat, link, how, ss)
    for plat, url, link, how, s in sorted(profiles.values(), key=lambda v: (v[0], v[1])):
        env.claim(sec, "company_profile", {"platform": plat, "url": url},
                  [env.cite(s, link, "company_profile: " + how)], confidence=0.95,
                  note="linked from the verified company website (%s)" % how)
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
                    n = news_items(env, ss, pg, rr.final_url)
                    if not n and pg is not None:
                        n = listing_items(env, ss, rr.text(), rr.final_url)
                    activity += n
                break
    for feed in (page.feeds or [])[:1]:
        rr, ss, why = site.get(feed)
        if rr is not None and rr.ok:
            activity += feed_items(env, ss, rr.text())
    if not activity and "/wp-content/" in r.text():
        # WordPress sites publish their posts through their own REST API: dated titles straight from the company.
        root = urlunsplit((urlsplit(r.final_url).scheme, urlsplit(r.final_url).netloc, "", "", ""))
        rr, ss, why = site.get(root + "/wp-json/wp/v2/posts?per_page=5&_fields=date,title,link")
        if rr is not None and rr.ok:
            activity += wp_posts(env, ss, rr)
    if not activity:
        activity += listing_items(env, snap, r.text(), r.final_url, home=True)   # latest news on the home page
    return {"profiles": len(profiles), "hiring": hiring, "activity": activity}


GENERIC_BRAND = {"hjem", "forside", "home", "startside", "velkommen", "welcome", "hovedside", "index", "untitled"}


def wp_posts(env: Envelope, snap, resp, limit: int = 5) -> int:
    import html as _html
    try:
        posts = resp.json()
    except ValueError:
        return 0
    n = 0
    for p in posts if isinstance(posts, list) else []:
        if not isinstance(p, dict):
            continue
        title = (p.get("title") or {}).get("rendered") if isinstance(p.get("title"), dict) else None
        title = " ".join(_html.unescape(re.sub(r"<[^>]+>", "", title or "")).split())
        date = (p.get("date") or "")[:10]
        if title and DATE_RE.match(date) and useful_item(title, date):
            raw = (p.get("title") or {}).get("rendered") or title
            env.claim("hiring_activity", "dated_activity", {"date": date, "title": title[:200], "url": p.get("link"),
                                                            "source": "company website (WordPress posts API)"},
                      [env.cite(snap, raw[:200], "WordPress post %s" % date)], effective_date=date)
            n += 1
            if n >= limit:
                break
    return n


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
        if title and date and useful_item(title, date) and (link or "").split("#")[0] not in activity_urls(env):
            env.claim("hiring_activity", "dated_activity", {"date": date, "title": title[:200], "url": link,
                                                            "source": "company website feed"},
                      [env.cite(snap, "%s %s" % (date, title[:200]), "RSS/Atom item")], effective_date=date)
            n += 1
            if n >= limit:
                break
    return n


def activity_urls(env: Envelope) -> set:
    return {str((c.get("value") or {}).get("url") or "").split("#")[0] for c in env.claims
            if c["field"] == "dated_activity" and c["availability"] == "available"} - {""}


MONTH_WORD = (r"(jan(?:uar|uary)?|feb(?:ruar|ruary)?|mar(?:s|ch)?|apr(?:il)?|mai|may|jun[ei]?|jul[iy]?|aug(?:ust)?|"
              r"sep(?:t|tember)?|o[ck]t(?:ober)?|nov(?:ember)?|de[sc](?:ember)?)")
MONTH_NO = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
            "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12}
TEXT_DATES = (
    (re.compile(r"(?<![\d.])(0?[1-9]|[12]\d|3[01])\.\s?(0?[1-9]|1[0-2])\.\s?(20[0-3]\d)(?!\d)"), "dmy"),
    (re.compile(r"(?<![\d.])(0?[1-9]|[12]\d|3[01])\.?\s+" + MONTH_WORD + r"\.?,?\s+(20[0-3]\d)(?!\d)", re.I), "dMy"),
    (re.compile(r"\b" + MONTH_WORD + r"\.?\s+(0?[1-9]|[12]\d|3[01]),?\s+(20[0-3]\d)(?!\d)", re.I), "Mdy"),
    (DATE_RE, "ymd"),
)


def text_dates(text: str) -> list[tuple[str, str]]:
    """Full dates printed in a text («12.10.2026», «12. oktober 2026», «October 12, 2026», «2026-10-12») as ISO."""
    from datetime import date as _date
    out = []
    for rx, kind in TEXT_DATES:
        for m in rx.finditer(text):
            g = m.groups()
            try:
                if kind == "dmy":
                    d = _date(int(g[2]), int(g[1]), int(g[0]))
                elif kind == "dMy":
                    d = _date(int(g[2]), MONTH_NO[fold(g[1])[:3]], int(g[0]))
                elif kind == "Mdy":
                    d = _date(int(g[2]), MONTH_NO[fold(g[0])[:3]], int(g[1]))
                else:
                    d = _date(int(g[0]), int(g[1]), int(g[2]))
            except (ValueError, KeyError):
                continue
            out.append((d.isoformat(), m.group(0)))
    return out


class Listing(HTMLParser):
    """Blocks of a news or blog listing: article and li elements, and div, section or a elements with a class or id token
    that ends in a word for a news item or card («news-item», «blog-single», «card», «post-123»; not «card-content»,
    «t2-post-title» or the utility «items-center»). Each block keeps its links (with their class), headings and title
    elements, <time datetime> values and visible text."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
            "wbr"}
    SKIP = {"script", "style", "noscript", "template", "nav", "svg", "select"}
    ITEM_TOKEN = re.compile(r"^[a-z0-9_-]*?(news|nyhet|nyheter|post|item|card|entry|article|artikkel|teaser|blog|innlegg|"
                            r"aktuelt|story|stories)(?:[-_]+(item|card|single|entry|teaser|preview|summary|block|box|"
                            r"wrap|wrapper|container|tile))?(?:[-_]*\d+)?s?$", re.I)
    TITLE_TOKEN = re.compile(r"(title|heading|headline|tittel|overskrift)$", re.I)

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.blocks: list[dict] = []
        self._stack: list[tuple[str, int | None, bool]] = []
        self._skip = None                     # stack depth at which a skipped element (script, nav ...) began
        self._href = None
        self._acls = ""
        self._atext: list[str] = []
        self._a_from = 0
        self._head = None                     # [text parts, href] inside h1-h4 or a title element

    def _block(self):
        for _, b, _ in reversed(self._stack):
            if b is not None:
                return self.blocks[b]
        return None

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        a = {k: (v or "") for k, v in attrs}
        tokens = (a.get("class", "") + " " + a.get("id", "")).split()
        b, head = None, False
        if self._skip is None and tag in self.SKIP:
            self._skip = len(self._stack)
        if self._skip is None:
            if tag in ("article", "li") or (tag in ("div", "section", "a") and
                                            any(self.ITEM_TOKEN.match(x) for x in tokens)):
                self.blocks.append({"links": [], "heads": [], "times": [], "text": []})
                b = len(self.blocks) - 1
            blk = self.blocks[b] if b is not None else self._block()
            if tag == "a" and a.get("href"):
                self._href, self._acls, self._atext = urljoin(self.base, a["href"].strip()), a.get("class", ""), []
                self._a_from = len(self.blocks)
            elif self._head is None and (tag in ("h1", "h2", "h3", "h4") or
                                         (tag != "a" and any(self.TITLE_TOKEN.search(x) for x in tokens))):
                self._head, head = [[], None], True
            elif tag == "time" and a.get("datetime") and blk is not None:
                m = DATE_RE.search(a["datetime"])
                if m:
                    blk["times"].append((m.group(0), a["datetime"]))
        self._stack.append((tag, b, head))

    def handle_endtag(self, tag):
        if tag in self.VOID or not any(t == tag for t, _, _ in self._stack):
            return
        while self._stack:
            t, _, head = self._stack[-1]
            self._close(t, head)
            self._stack.pop()
            if self._skip is not None and len(self._stack) <= self._skip:
                self._skip = None
            if t == tag:
                break

    def _close(self, t, head):
        if self._skip is not None:
            return
        if t == "a" and self._href is not None:
            link = (self._href, " ".join(" ".join(self._atext).split()), self._acls)
            blk = self._block()
            if blk is not None:
                blk["links"].append(link)
            for nb in self.blocks[self._a_from:]:
                if nb is not blk and not nb["links"]:
                    nb["links"].append(link)          # a card that is one link as a whole
            if self._head is not None and self._head[1] is None:
                self._head[1] = self._href
            self._href = None
        elif head and self._head is not None:
            text = " ".join(" ".join(self._head[0]).split())
            blk = self._block()
            if blk is not None and text:
                blk["heads"].append((text, self._head[1]))
            self._head = None

    def handle_data(self, data):
        if self._skip is not None:
            return
        if self._href is not None:
            self._atext.append(data)
        if self._head is not None:
            self._head[0].append(data)
        blk = self._block()
        if blk is not None:
            blk["text"].append(data)


GENERIC_LINK = re.compile(r"^(les mer|les hele (saken|artikkelen|innlegget)|les videre|mer|more|read more|read on|se mer|"
                          r"vis mer|vis alle|se alle|alle nyheter|continue reading|fortsett å lese|klikk her|click here|"
                          r"\W*)\W*$", re.I)
NOT_ITEM = re.compile(r"/(tag|tags|stikkord|category|categories|kategori|kategorier|author|forfatter|page|side)/|"
                      r"[?&](page|side|p)=\d", re.I)
TAG_LINK = re.compile(r"(categor|kategori|(^|[-_\s])tags?($|[-_\s])|label|badge|chip)", re.I)
NEWS_PATH = re.compile(r"(/|-)(nyhet|nyheter|aktuelt|news|blogg?|presse|press|artikler|artikkel|articles?|posts?|"
                       r"innlegg|stories|story|saker|sak|insights?|info-hub|pressemelding\w*|press-releases?)(/|-|$)|"
                       r"/20[0-3]\d/(0?[1-9]|1[0-2])/|20[0-3]\d-(0[1-9]|1[0-2])-", re.I)
SHORT_DATE = re.compile(r"(?<![\d.])(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.(\d{2})(?![\d.])")


def own_item_page(href: str, host: str, here: str) -> bool:
    s = urlsplit(href)
    path = s.path.rstrip("/")
    return s.scheme in ("http", "https") and site_key(s.hostname) == host and path not in ("", here) \
        and not NOT_ITEM.search(href) and not re.search(r"\.(jpe?g|png|gif|webp|svg|css|js|zip)$", path, re.I)


def good_title(t: str) -> bool:
    return len(t) >= 12 and len(t.split()) >= 2 and not GENERIC_LINK.match(t) and \
        len(re.sub(r"[\W\d_]", "", t)) >= 8


def listing_items(env: Envelope, snap, html_text: str, url: str, limit: int = 5, home: bool = False) -> int:
    """Dated items of a news or blog listing on the verified site, for sites without feed or JSON-LD: a block with
    exactly one date (its <time datetime>, else the one full date printed in it), links to exactly one other page of
    the same site, and a title (its heading or title element, else the link text without dates); category and tag
    links never count. Blocks with two different dates or links to several pages are skipped rather than guessed. On the home page (home=True), where
    offers and events carry dates too, only blocks with a <time datetime> or a link to a news-like path count."""
    p = Listing(url)
    try:
        p.feed(html_text)
        p.close()
    except Exception:
        return 0
    host, here = site_key(urlsplit(url).hostname), urlsplit(url).path.rstrip("/")
    seen, items = activity_urls(env), []
    for b in p.blocks:
        links = [(h, t) for h, t, cls in b["links"] if own_item_page(h, host, here) and not TAG_LINK.search(cls)]
        if not links:
            continue
        dates = {iso: (raw, "time element") for iso, raw in b["times"]}
        if not dates:
            dates = {iso: (raw, "printed date") for iso, raw in text_dates(" ".join(" ".join(b["text"]).split()))}
        if len(dates) != 1:
            continue
        (iso, (raw, how)), = dates.items()
        urls = {h.split("#")[0] for h, _ in links}
        paths = {u: urlsplit(u).path.rstrip("/") for u in urls}
        urls = {u for u in urls if not any(paths[v].startswith(paths[u] + "/") for v in urls)}   # not its section
        if len(urls) != 1:
            continue                                  # a container of several items: its one date fits only one
        (href,) = urls
        links = [(h, t) for h, t in links if h.split("#")[0] == href]
        heads = list(dict.fromkeys(t for t, h in b["heads"] if good_title(t) and not text_dates(t)))
        if len(heads) > 1:
            heads = list(dict.fromkeys(t for t, h in b["heads"] if good_title(t) and not text_dates(t) and h and
                                       h.split("#")[0] == href))
            if len(heads) != 1:
                continue
        title = heads[0] if heads else None
        if title is None:
            cand = []
            for h, t in links:
                t = " ".join(SHORT_DATE.sub(" ", t).split())
                for _, d in text_dates(t):
                    t = " ".join(t.replace(d, " ").split())
                if good_title(t):
                    cand.append((t, h))
            if not cand:
                continue
            title = max(cand, key=lambda x: len(x[0]))[0]
        if home and how != "time element" and not NEWS_PATH.search(urlsplit(href).path):
            continue
        if href in seen or not useful_item(title, iso):
            continue
        seen.add(href)
        items.append((iso, title, href, raw, how))
    items.sort(key=lambda x: x[0], reverse=True)
    for iso, title, href, raw, how in items[:limit]:
        env.claim("hiring_activity", "dated_activity", {"date": iso, "title": title[:200], "url": href,
                                                        "source": "company website (news listing)"},
                  [env.cite(snap, title[:200], "news listing item; date from %s «%s»" % (how, raw[:40]))],
                  effective_date=iso)
    return min(len(items), limit)
