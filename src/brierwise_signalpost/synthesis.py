"""Decision-useful synthesis from the envelope's own claims: every sentence carries the evidence ids it rests on.

Deterministic templates, no language model: nothing is said that a claim does not support, and unknowns are named.
"""
from __future__ import annotations

from collections import defaultdict

from .official import ACCOUNT_TYPE_TEXT

STATUS_TEXT = {"bankrupt": "The register marks it as bankrupt.",
               "liquidating": "The register marks it as being wound up (under liquidation).",
               "forced_dissolution": "The register marks it as under forced dissolution."}
ROLE_LABEL = {"DAGL": "daily manager (CEO)", "LEDE": "board chair", "INNH": "owner", "DTPR": "partner",
              "DTSO": "partner", "REVI": "auditor", "REGN": "accountant", "FFØR": "business manager"}


FORM_EN = {"AS": "private limited company", "ASA": "public limited company", "ENK": "sole proprietorship",
           "NUF": "Norwegian branch of a foreign company", "SA": "cooperative", "BRL": "housing cooperative",
           "BBL": "housing building cooperative", "ESEK": "property co-ownership", "ANS": "general partnership",
           "DA": "partnership with shared liability", "KS": "limited partnership", "FLI": "association",
           "STI": "foundation", "SAM": "jointly owned entity", "IKS": "inter-municipal company",
           "KF": "municipal enterprise", "SF": "state enterprise", "PK": "pension fund", "SE": "European company"}


def form_text(value: dict) -> str:
    code = value.get("code") or ""
    label = FORM_EN.get(code) or (value.get("label") or code).lower()
    return "%s %s (%s)" % ("an" if label[:1] in "aeiou" else "a", label, code) if code else "an entity"


def nok(v) -> str:
    """A rounded amount in words (the currency is added by `money`)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    a = abs(x)
    if a >= 1e9:
        s = "%.1f billion" % (x / 1e9)
    elif a >= 1e6:
        s = "%.1f million" % (x / 1e6)
    elif a >= 1e3:
        s = "%.0f thousand" % (x / 1e3)
    else:
        s = "%.0f" % x
    return s


def money(v: dict) -> str:
    """Amount with the currency the accounts were filed in (some groups file in USD or EUR)."""
    return "%s %s" % (v.get("currency") or "", nok(v.get("amount"))) if v.get("currency") else nok(v.get("amount"))


def period_end(c: dict) -> str:
    return str((c.get("reporting_period") or "").split("/")[-1])


def latest_accounts(claims: list, kind: str = "selskap") -> list:
    """Available accounts periods of one type, newest first."""
    rows = [c for c in claims if c["field"] == "accounts_period" and c["availability"] == "available" and
            (c["value"] or {}).get("accounts_type") == kind]
    return sorted(rows, key=period_end, reverse=True)


def clip(text: str, n: int = 240) -> str:
    """Quoted text in one line, cut at a sentence end in the second half of the limit, else at a word, never mid-word."""
    t = " ".join((text or "").split())
    if len(t) <= n:
        return t
    cut = t[:n]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= n // 2:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(" ,;:-") + " …"


def synthesize(env) -> dict:
    by = defaultdict(list)
    for c in env.claims:
        by[c["field"]].append(c)

    def av(field):
        return [c for c in by.get(field, []) if c["availability"] == "available"]

    def first(field):
        x = av(field)
        return x[0] if x else None

    out, unknown = [], []

    def say(key, text, claims):
        ids = sorted({i for c in claims if c for i in c["evidence_ids"]}, key=lambda s: int(s.split("-")[1]))
        out.append({"topic": key, "text": text, "evidence_ids": ids})

    name, form, reg = first("legal_name"), first("organisation_form"), first("registered_date")
    muni, ind, status = first("municipality"), first("industry_codes"), first("registry_status")
    if name:
        parts = ["%s is" % name["value"]]
        parts.append(form_text(form["value"]) if form else "an entity")
        if reg:
            parts.append("registered on %s" % reg["value"])
        if muni:
            parts.append("in %s" % muni["value"]["name"].title())
        text = " ".join(parts)
        if ind:
            labels = ["%s (%s)" % (i["label"], i["code"]) for i in ind["value"]]
            text += ", classified under " + "; ".join(labels)
        say("identity", text + ".", [name, form, reg, muni, ind])
    if status and status["value"] in STATUS_TEXT:
        say("status", STATUS_TEXT[status["value"]], [status])
    purpose, desc = first("registered_purpose"), first("website_description")
    if purpose:
        p = clip(purpose["value"])
        say("purpose", "Registered purpose: «%s»%s" % (p, "" if p[-1:] in ".!?…" else "."), [purpose])
    if desc:
        say("self_description", "Its own website describes it as: «%s» (company-owned text, not independent)." %
            clip(desc["value"]), [desc])
    # numbers: the newest company accounts (group accounts only when no company accounts were returned), with the
    # previous year for comparison and the currency they were filed in
    def value_of(f, kind, p):
        return next((x for x in av(f) if x.get("reporting_period") == p and x["value"].get("accounts_type") == kind),
                    None)

    periods = latest_accounts(env.claims) or latest_accounts(env.claims, "konsern")
    if periods:
        period = periods[0]
        kind, p = period["value"].get("accounts_type"), period["reporting_period"]
        prior = periods[1] if len(periods) > 1 else None
        bits, used = [], [period]
        for f, label in (("revenue", "revenue"), ("payroll_costs", "payroll costs"),
                         ("operating_result", "operating result"), ("net_result", "net result"), ("equity", "equity"),
                         ("total_assets", "total assets")):
            c = value_of(f, kind, p)
            if not c:
                continue
            text = "%s %s" % (label, money(c["value"]))
            used.append(c)
            if prior and f in ("revenue", "net_result"):
                b = value_of(f, kind, prior["reporting_period"])
                if b and b["value"].get("currency") == c["value"].get("currency"):
                    used.append(b)
                    x, y = c["value"]["amount"], b["value"]["amount"]
                    year = period_end(prior)[:4]
                    if f == "revenue" and y > 0 and x >= 0:
                        text += " (%s: %s, %s)" % (year, money(b["value"]), trend(x, y))
                    else:
                        text += " (%s: %s)" % (year, money(b["value"]))
            bits.append(text)
        if bits:
            text = "Latest filed %s (%s): %s." % (ACCOUNT_TYPE_TEXT.get(kind, kind), p.replace("/", " to "),
                                                  ", ".join(bits))
            eq = value_of("equity", kind, p)
            if eq and eq["value"]["amount"] < 0:
                text += " Equity is negative."
            if period["value"].get("not_audited"):
                text += " The accounts are not audited."
            if period.get("note"):
                text += " Note: %s." % period["note"]
            years = sorted({period_end(c)[:4] for c in periods})
            if len(years) > 1:
                text += " The open accounts API returns %s for %s." % (ACCOUNT_TYPE_TEXT.get(kind, kind),
                                                                       ", ".join(years))
            say("accounts", text, used)
        group = latest_accounts(env.claims, "konsern") if kind == "selskap" else []
        if group:
            g, gp = group[0], group[0]["reporting_period"]
            gbits, gused = [], [g]
            for f, label in (("revenue", "revenue"), ("net_result", "net result"), ("equity", "equity")):
                c = value_of(f, "konsern", gp)
                if c:
                    gbits.append("%s %s" % (label, money(c["value"])))
                    gused.append(c)
            if gbits:
                say("accounts", "It also files group accounts as parent company (%s): %s." % (
                    gp.replace("/", " to "), ", ".join(gbits)), gused)
    else:
        acc = by.get("annual_accounts", [])
        if acc:
            unknown.append(("annual accounts", acc[0]["availability"], acc))
    # leadership and workplaces
    roles = [c for c in av("role")]
    if roles:
        named = []
        for code in ("DAGL", "LEDE", "INNH", "DTPR", "DTSO"):
            for c in roles:
                if c["value"].get("role_code") == code:
                    named.append("%s: %s" % (ROLE_LABEL[code], c["value"]["name"]))
        members = sum(1 for c in roles if c["value"].get("role_code") == "MEDL")
        audit = [c["value"]["name"] for c in roles if c["value"].get("role_code") == "REVI"]
        text = "; ".join(named) if named else "Registered role holders are listed"
        if members:
            text += "; %d further board member%s" % (members, "s" if members > 1 else "")
        if audit:
            text += "; auditor: %s" % audit[0]
        say("leadership", text[0].upper() + text[1:] + ".", roles)
    wp = av("registered_workplace")
    if wp:
        towns = sorted({c["value"].get("municipality") or "" for c in wp} - {""})
        say("workplaces", "%d registered workplace%s%s." % (len(wp), "s" if len(wp) > 1 else "",
                                                           (" in " + ", ".join(t.title() for t in towns[:6]) +
                                                            (" and more" if len(towns) > 6 else "")) if towns else ""),
            wp)
    emp, hist = first("registered_employees"), first("registered_employees_history")
    if emp:
        text = "The register reports %s employees" % emp["value"]
        series = (hist or {}).get("value") or []
        if len(series) >= 2 and series[0]["employees"] != series[-1]["employees"]:
            a, b = series[0], series[-1]
            text += ", %s from %s on %s to %s on %s" % ("up" if b["employees"] > a["employees"] else "down",
                                                       a["employees"], a["date"], b["employees"], b["date"])
        elif len(series) >= 2:
            text += ", unchanged since %s" % series[0]["date"]
        say("employees", text + ".", [emp, hist])
    # online presence
    site, profs, brand = first("official_website"), av("company_profile"), first("public_brand")
    if site:
        text = "Official website: %s (%s)." % (site["value"], site.get("note") or "verified")
        if brand:
            text += " The site presents the company as «%s»." % brand["value"]
        if profs:
            text += " Company-owned profiles linked from it: %s." % ", ".join(sorted(c["value"]["platform"].title()
                                                                                     for c in profs))
        say("online", text, [site, brand] + profs)
    else:
        if profs:
            say("online", "Company-owned profile given in the register: %s." % ", ".join(
                "%s (%s)" % (c["value"]["platform"].title(), c["value"]["url"]) for c in profs), profs)
        w = by.get("official_website", [])
        if w:
            unknown.append(("official website", w[0]["availability"], w))
    # hiring and activity
    jobs, careers, total = av("job_posting"), first("careers_page"), first("job_postings_total")
    # absence of ads only when the whole feed was read (otherwise the claim is failed, blocked or missing)
    nav_checked = any(c["availability"] == "not_available" for c in by.get("job_postings", []))
    if jobs:
        latest = max((c["value"].get("published") or "") for c in jobs)
        n = total["value"] if total else len(jobs)
        places = sorted({l.split(",")[0] for c in jobs for l in c["value"].get("locations") or []})
        say("hiring", "%d active job ad%s on Nav's official job feed%s, latest published %s%s." % (
            n, "s" if n > 1 else "", " (%d most recent listed)" % len(jobs) if total else "", latest or "unknown",
            (", in " + ", ".join(p.title() for p in places[:4]) + (" and more" if len(places) > 4 else ""))
            if places else ""), jobs + ([total] if total else []))
    elif careers:
        say("hiring", "Its website has a careers page (%s); %s." % (
            careers["value"], "no active ad was found on Nav's official feed" if nav_checked else
            "Nav's official feed was not read in full in this run, so its ads are unknown"),
            [careers] + by.get("job_postings", []))
    else:
        j = by.get("job_postings", [])
        if j:
            unknown.append(("hiring", j[0]["availability"], j))
    acts = sorted(av("dated_activity"), key=lambda c: c["value"].get("date") or "", reverse=True)
    if acts:
        a = acts[0]["value"]
        say("activity", "Most recent dated activity on its own channels: %s, «%s»%s." % (
            a["date"], a["title"][:120], " (%d dated items found)" % len(acts) if len(acts) > 1 else ""), acts[:3])
    elif site:
        unknown.append(("dated public activity", "not_available", [site]))
    if not emp:
        e = by.get("registered_employees", [])
        latest = latest_accounts(env.claims)
        pay = value_of("payroll_costs", "selskap", latest[0]["reporting_period"]) if latest else None
        if pay and pay["value"]["amount"] > 0:
            say("employees", "The register publishes no employee count, but the %s accounts show payroll costs of %s." %
                (period_end(pay)[:4], money(pay["value"])), e + [pay])
        elif e:
            unknown.append(("number of employees", e[0]["availability"], e))
    # a parent company's own accounts say little about the business: the signals use its group accounts
    signals(out, latest_accounts(env.claims, "konsern") or periods, value_of, emp, hist, jobs, total,
            careers, nav_checked, acts, say)
    # unknowns
    labels = {"not_available": "not found in the checked sources", "blocked": "the source refused the request",
              "failed": "the source could not be read in this run", "ambiguous": "a candidate could not be tied to "
              "this exact company", "not_applicable": "does not apply"}
    for what, state, claims in unknown:
        note = next((c.get("note") for c in claims if c.get("note")), None) if what in (
            "hiring", "official website", "annual accounts") else None
        say("unknown", "Unknown %s: %s%s." % (what, labels.get(state, state), "; " + note if note else ""), claims)
    return {"method": "deterministic templates over this envelope's claims; no language model",
            "sentences": out}


def trend(new: float, old: float) -> str:
    pct = round(100.0 * (new - old) / old)
    return "unchanged" if pct == 0 else ("up %d%%" % pct if pct > 0 else "down %d%%" % -pct)


def signals(out, periods, value_of, emp, hist, jobs, total, careers, nav_checked, acts, say) -> None:
    """One line up front with what a job seeker, seller, partner or investor looks at first: direction of revenue,
    operating result, equity against total assets (of the group when the company files group accounts), staff and its
    trend, hiring and the latest own news. Only claims of this envelope; the shares are computed from the filed
    figures and say so."""
    sig, used, group = [], [], ""
    if periods:
        kind, p = periods[0]["value"].get("accounts_type"), periods[0]["reporting_period"]
        year, group = period_end(periods[0])[:4], "group " if kind == "konsern" else ""
        rv, op, eq, ta = (value_of(f, kind, p) for f in ("revenue", "operating_result", "equity", "total_assets"))
        if rv:
            text = "%srevenue %s in %s" % (group, money(rv["value"]), year)
            b = value_of("revenue", kind, periods[1]["reporting_period"]) if len(periods) > 1 else None
            if b and b["value"].get("currency") == rv["value"].get("currency") and b["value"]["amount"] > 0 and \
                    rv["value"]["amount"] >= 0:
                text += " (%s on %s)" % (trend(rv["value"]["amount"], b["value"]["amount"]), period_end(periods[1])[:4])
                used.append(b)
            sig.append(text)
            used.append(rv)
        if op:
            x = op["value"]["amount"]
            sig.append("%soperating %s of %s" % (group, "loss" if x < 0 else "profit",
                                                 money(dict(op["value"], amount=abs(x))))
                       if x else "%soperating result zero" % group)
            used.append(op)
        if eq and eq["value"]["amount"] < 0:
            sig.append("negative %sequity" % group)
            used.append(eq)
        elif eq and ta and ta["value"]["amount"] > 0 and eq["value"].get("currency") == ta["value"].get("currency"):
            sig.append("%sequity %d%% of total assets (computed from the filed figures)" %
                       (group, round(100.0 * eq["value"]["amount"] / ta["value"]["amount"])))
            used += [eq, ta]
    if emp:
        text = "%s employees%s" % (emp["value"], " in this entity" if group else "")
        series = (hist or {}).get("value") or []
        if len(series) >= 2 and series[0]["employees"]:
            text += " (%s since %s)" % (trend(series[-1]["employees"], series[0]["employees"]), series[0]["date"])
        sig.append(text)
        used += [emp, hist]
    if jobs:
        n = total["value"] if total else len(jobs)
        sig.append("hiring: %d active job ad%s on Nav" % (n, "s" if n > 1 else ""))
        used += jobs[:3] + ([total] if total else [])
    elif careers:
        sig.append("careers page, no active job ad on Nav" if nav_checked else "careers page")
        used.append(careers)
    if acts:
        sig.append("latest own news %s" % acts[0]["value"]["date"])
        used.append(acts[0])
    if len(sig) < 2:
        return
    say("signals", "Key signals: %s." % "; ".join(sig), used)
    # up front, after the identity (and a register status that overrides everything else)
    at = sum(1 for s in out if s["topic"] in ("identity", "status"))
    out.insert(at, out.pop())


FIELD_TEXT = {"legal_name": "legal name", "organisation_form": "legal form", "registry_status": "register status",
              "business_address": "business address", "postal_address": "postal address",
              "industry_codes": "industry classification", "registered_employees": "registered employee count",
              "registry_website": "website in the register", "official_website": "official website",
              "share_capital": "share capital", "registered_purpose": "registered purpose",
              "part_of_group": "group membership", "role": "role holder", "registered_workplace": "registered workplace",
              "company_profile": "company-owned profile", "job_posting": "job ad", "dated_activity": "dated item", "register_event": "register event",
              "public_brand": "public brand", "revenue": "revenue", "operating_result": "operating result",
              "net_result": "net result", "equity": "equity", "total_assets": "total assets",
              "total_debt": "total debt", "profit_before_tax": "profit before tax", "payroll_costs": "payroll costs",
              "sales_revenue": "sales revenue", "cash_and_bank": "cash and bank deposits", "tax_expense": "tax expense"}


def _short(v) -> str:
    if isinstance(v, dict):
        if v.get("amount") is not None:
            return money(v)
        if v.get("event"):
            return "%s %s" % (v.get("date") or "", v["event"])
        bits = [str(v[k]) for k in ("role", "name", "title", "platform", "url", "label", "address") if v.get(k)]
        return ", ".join(bits[:2]) or "a new value"
    if isinstance(v, list):
        return "; ".join(_short(x) for x in v[:3])
    return str(v)[:100]


def change_sentences(changes: list, prev: dict | None, claims: list) -> list:
    """What changed: material changes against the previous run, and dated changes the register itself records."""
    out = []
    if prev:
        when = ((prev.get("run") or {}).get("completed_at") or "")[:10] or "the previous run"
        if changes:
            parts = []
            for c in changes[:5]:
                label = FIELD_TEXT.get(c["field"], c["field"].replace("_", " "))
                if c["change"] == "changed":
                    parts.append("%s changed from %s to %s" % (label, _short(c["before"]), _short(c["after"])))
                elif c["change"] == "added":
                    parts.append("new %s: %s" % (label, _short(c["after"])))
                else:
                    parts.append("%s no longer found: %s" % (label, _short(c["before"])))
            more = " and %d more" % (len(changes) - 5) if len(changes) > 5 else ""
            ids = sorted({i for c in changes for i in c.get("evidence_ids") or []}, key=lambda s: int(s.split("-")[1]))
            out.append({"topic": "changes", "text": "Since the previous run (%s): %s%s." % (when, "; ".join(parts),
                                                                                         more), "evidence_ids": ids})
        else:
            out.append({"topic": "changes", "text": "No material change since the previous run (%s)." % when,
                        "evidence_ids": []})
        kept = [c for c in claims if c.get("carried_forward")]
        if kept:
            labels = sorted({FIELD_TEXT.get(c["field"], c["field"].replace("_", " ")) for c in kept})
            out.append({"topic": "changes", "text": "Not re-checked in this run because the source failed, kept from the "
                        "previous run (%s): %s." % (when, ", ".join(labels)),
                        "evidence_ids": sorted({i for c in kept for i in c["evidence_ids"]},
                                               key=lambda s: int(s.split("-")[1]))})
    else:
        out.append({"topic": "changes", "text": "First run for this company: there is no earlier snapshot yet, so the "
                    "next refresh will report what changed.", "evidence_ids": []})
    events = sorted((c for c in claims if c["field"] == "register_event" and c["availability"] == "available"),
                    key=lambda c: c["value"].get("date") or "", reverse=True)
    if events:
        out.append({"topic": "changes", "text": "Latest changes in the official register: %s." % "; ".join(
            "%s %s" % (c["value"]["date"], c["value"]["event"]) for c in events[:3]),
            "evidence_ids": sorted({i for c in events[:3] for i in c["evidence_ids"]},
                                   key=lambda s: int(s.split("-")[1]))})
    former = next((c for c in claims if c["field"] == "former_names" and c["availability"] == "available"), None)
    if former and former["value"]:
        last = sorted(former["value"], key=lambda n: n.get("to") or "")[-1]
        out.append({"topic": "changes", "text": "The register records an earlier name, %s, used until %s." % (
            last["name"], last.get("to") or "an unknown date"), "evidence_ids": former["evidence_ids"]})
    return out


def with_changes(synthesis: dict, changes: list, prev: dict | None, claims: list) -> dict:
    """Insert the change sentences before the unknowns."""
    sents = synthesis.get("sentences") or []
    known = [s for s in sents if s.get("topic") != "unknown"]
    unknown = [s for s in sents if s.get("topic") == "unknown"]
    return dict(synthesis, sentences=known + change_sentences(changes, prev, claims) + unknown)
