"""Decision-useful synthesis from the envelope's own claims: every sentence carries the evidence ids it rests on.

Deterministic templates, no language model: nothing is said that a claim does not support, and unknowns are named.
"""
from __future__ import annotations

from collections import defaultdict

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
        p = purpose["value"]
        say("purpose", "Registered purpose: «%s»." % (p if len(p) <= 240 else p[:237].rsplit(" ", 1)[0] + " …"),
            [purpose])
    if desc:
        say("self_description", "Its own website describes it as: «%s» (company-owned text, not independent)." %
            desc["value"][:240], [desc])
    # numbers
    period = first("accounts_period")
    if period:
        p = period["reporting_period"]
        bits, used = [], [period]
        for f, label in (("revenue", "revenue"), ("operating_result", "operating result"),
                         ("net_result", "net result"), ("equity", "equity"), ("total_assets", "total assets")):
            c = next((x for x in av(f) if x.get("reporting_period") == p), None)
            if c:
                bits.append("%s NOK %s" % (label, nok(c["value"]["amount"])))
                used.append(c)
        if bits:
            text = "Latest filed accounts (%s, %s): %s." % (p.replace("/", " to "),
                                                            period["value"].get("accounts_type", "company"),
                                                            ", ".join(bits))
            eq = next((x for x in av("equity") if x.get("reporting_period") == p), None)
            if eq and eq["value"]["amount"] < 0:
                text += " Equity is negative."
            if period["value"].get("not_audited"):
                text += " The accounts are not audited."
            say("accounts", text, used)
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
    emp = first("registered_employees")
    if emp:
        say("employees", "The register reports %s employees." % emp["value"], [emp])
    # online presence
    site, profs = first("official_website"), av("company_profile")
    if site:
        text = "Official website: %s (%s)." % (site["value"], site.get("note") or "verified")
        if profs:
            text += " Company-owned profiles linked from it: %s." % ", ".join(sorted(c["value"]["platform"].title()
                                                                                     for c in profs))
        say("online", text, [site] + profs)
    else:
        w = by.get("official_website", [])
        if w:
            unknown.append(("official website", w[0]["availability"], w))
    # hiring and activity
    jobs, careers = av("job_posting"), first("careers_page")
    if jobs:
        latest = max((c["value"].get("published") or "") for c in jobs)
        say("hiring", "%d active job ad%s on Nav's official job feed, latest published %s." % (
            len(jobs), "s" if len(jobs) > 1 else "", latest or "unknown"), jobs)
    elif careers:
        say("hiring", "Its website has a careers page (%s); no active ad was found on Nav's official feed." %
            careers["value"], [careers] + by.get("job_postings", []))
    else:
        j = by.get("job_postings", [])
        if j:
            unknown.append(("hiring", j[0]["availability"], j))
    acts = sorted(av("dated_activity"), key=lambda c: c["value"].get("date") or "", reverse=True)
    if acts:
        a = acts[0]["value"]
        say("activity", "Most recent dated activity on its own channels: %s, «%s»." % (a["date"], a["title"][:120]),
            acts[:1])
    # unknowns
    labels = {"not_available": "not found in the checked sources", "blocked": "the source refused the request",
              "failed": "the source could not be read in this run", "ambiguous": "a candidate could not be tied to "
              "this exact company", "not_applicable": "does not apply"}
    for what, state, claims in unknown:
        say("unknown", "Unknown %s: %s." % (what, labels.get(state, state)), claims)
    return {"method": "deterministic templates over this envelope's claims; no language model",
            "sentences": out}
