"""Official layer: Brønnøysund Register Centre (Enhetsregisteret, Regnskapsregisteret), NLOD 2.0.

Person data is used only in the company-centric role context: name and function as published by the register.
Dates of birth are never read into claims, stored or displayed.
"""
from __future__ import annotations

from .model import Envelope, state_of_status

BRREG = "https://data.brreg.no"
ROLE_ORDER = ["DAGL", "LEDE", "NEST", "MEDL", "VARA", "INNH", "DTPR", "DTSO", "KOMP", "BEST", "FFØR", "REVI", "REGN",
              "KONT", "OBS", "SAM"]


def _join(parts) -> str:
    return " ".join(p for p in (parts or []) if p)


def address_text(a: dict | None) -> str | None:
    if not a:
        return None
    line = ", ".join(x for x in a.get("adresse") or [] if x)
    tail = " ".join(x for x in (a.get("postnummer"), a.get("poststed")) if x)
    land = a.get("land") if a.get("landkode") not in (None, "NO") else None
    return ", ".join(x for x in (line, tail, land) if x) or None


def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def fetch_entity(client, env: Envelope) -> dict | None:
    """Live entity record; adds legal-identity claims. Returns the parsed record or None."""
    url = "%s/enhetsregisteret/api/enheter/%s" % (BRREG, env.orgnr)
    r = client.get(url, accept="application/json")
    snap = env.snapshot(r, "official_registry", "brreg_enheter_json")
    if not r.ok:
        state = "not_available" if r.status in (404, 410) else state_of_status(r.status)
        ev = env.cite(snap, "HTTP %s" % r.status)
        env.claim("legal_identity", "legal_name", None, [ev], state,
                  note="entity deleted from the register (HTTP 410)" if r.status == 410 else None)
        if state != "not_available":
            env.error("official.entity", r.error or "HTTP %s" % r.status, url)
        return None
    try:
        e = r.json()
    except ValueError as exc:
        env.error("official.entity", "invalid JSON: %s" % exc, url)
        env.claim("legal_identity", "legal_name", None, [env.cite(snap)], "failed")
        return None

    env.legal_name = e.get("navn")

    def add(field, value, span=None, **kw):
        if value in (None, "", [], {}):
            return
        env.claim("legal_identity", field, value, [env.cite(snap, span if span is not None else value, field)], **kw)

    add("legal_name", e.get("navn"), "navn: %s" % e.get("navn"))
    form = e.get("organisasjonsform") or {}
    add("organisation_form", {"code": form.get("kode"), "label": form.get("beskrivelse")},
        "organisasjonsform: %s %s" % (form.get("kode"), form.get("beskrivelse")))
    add("registered_date", e.get("registreringsdatoEnhetsregisteret"),
        "registreringsdatoEnhetsregisteret: %s" % e.get("registreringsdatoEnhetsregisteret"),
        effective_date=e.get("registreringsdatoEnhetsregisteret"))
    add("founded_date", e.get("stiftelsesdato"), "stiftelsesdato: %s" % e.get("stiftelsesdato"),
        effective_date=e.get("stiftelsesdato"))
    flags = {k: bool(e.get(k)) for k in ("konkurs", "underAvvikling", "underTvangsavviklingEllerTvangsopplosning")}
    status = "bankrupt" if flags["konkurs"] else ("forced_dissolution" if flags[
        "underTvangsavviklingEllerTvangsopplosning"] else ("liquidating" if flags["underAvvikling"] else "active"))
    add("registry_status", status, "konkurs=%s underAvvikling=%s underTvangsavvikling=%s" % (
        flags["konkurs"], flags["underAvvikling"], flags["underTvangsavviklingEllerTvangsopplosning"]))
    codes = []
    for k in ("naeringskode1", "naeringskode2", "naeringskode3"):
        n = e.get(k)
        if n and n.get("kode"):
            codes.append({"code": n.get("kode"), "label": n.get("beskrivelse")})
    add("industry_codes", codes, "; ".join("%s %s" % (c["code"], c["label"]) for c in codes))
    purpose = _join(e.get("vedtektsfestetFormaal")) or _join(e.get("aktivitet"))
    add("registered_purpose", purpose, purpose[:400] if purpose else None)
    if e.get("harRegistrertAntallAnsatte") and e.get("antallAnsatte") is not None:
        add("registered_employees", e.get("antallAnsatte"), "antallAnsatte: %s" % e.get("antallAnsatte"),
            effective_date=e.get("registreringsdatoAntallAnsatteEnhetsregisteret"))
    else:
        env.claim("legal_identity", "registered_employees", None, [env.cite(snap, "harRegistrertAntallAnsatte: %s" %
                  e.get("harRegistrertAntallAnsatte"), "registered_employees")], "not_available",
                  note="the register publishes no employee count for this entity")
    add("business_address", address_text(e.get("forretningsadresse")))
    add("postal_address", address_text(e.get("postadresse")))
    fa = e.get("forretningsadresse") or {}
    add("municipality", {"name": fa.get("kommune"), "number": fa.get("kommunenummer")} if fa.get("kommune") else None,
        "kommune: %s %s" % (fa.get("kommune"), fa.get("kommunenummer")))
    add("registry_website", e.get("hjemmeside"), "hjemmeside: %s" % e.get("hjemmeside"))
    # Registered e-mail, phone and mobile are left out: for small firms they are often a person's private contact.
    add("vat_registered", bool(e.get("registrertIMvaregisteret")), "registrertIMvaregisteret: %s" %
        e.get("registrertIMvaregisteret"))
    add("business_register", bool(e.get("registrertIForetaksregisteret")), "registrertIForetaksregisteret: %s" %
        e.get("registrertIForetaksregisteret"), effective_date=e.get("registreringsdatoForetaksregisteret"))
    cap = e.get("kapital") or {}
    if cap.get("belop") is not None:
        add("share_capital", {"amount": cap.get("belop"), "currency": cap.get("valuta"), "type": cap.get("type"),
                              "shares": cap.get("antallAksjer")},
            "kapital: %s %s" % (cap.get("belop"), cap.get("valuta")), effective_date=cap.get("innfortDato"))
    names = [{"name": h.get("navn"), "from": (h.get("fraDato") or "")[:10], "to": (h.get("tilDato") or "")[:10]}
             for h in e.get("historiskeNavn") or [] if h.get("navn")]
    add("former_names", names, "; ".join(n["name"] for n in names))
    if e.get("erIKonsern") is not None:
        add("part_of_group", bool(e.get("erIKonsern")), "erIKonsern: %s" % e.get("erIKonsern"))
    add("latest_submitted_accounts_year", e.get("sisteInnsendteAarsregnskap"),
        "sisteInnsendteAarsregnskap: %s" % e.get("sisteInnsendteAarsregnskap"))
    add("parent_unit", e.get("overordnetEnhet"), "overordnetEnhet: %s" % e.get("overordnetEnhet"))
    return e


def fetch_roles(client, env: Envelope) -> list[dict]:
    url = "%s/enhetsregisteret/api/enheter/%s/roller" % (BRREG, env.orgnr)
    r = client.get(url, accept="application/json")
    snap = env.snapshot(r, "official_registry", "brreg_roller_json")
    if not r.ok:
        state = state_of_status(r.status)
        env.claim("leadership_workplaces", "roles", None, [env.cite(snap, "HTTP %s" % r.status)], state)
        if state != "not_available":
            env.error("official.roles", r.error or "HTTP %s" % r.status, url)
        return []
    try:
        data = r.json()
    except ValueError as exc:
        env.error("official.roles", "invalid JSON: %s" % exc, url)
        return []
    found = []
    for group in data.get("rollegrupper") or []:
        changed = (group.get("sistEndret") or "")[:10] or None
        for role in group.get("roller") or []:
            if role.get("fratraadt") or role.get("avregistrert"):
                continue
            rtype = role.get("type") or {}
            item = {"role_code": rtype.get("kode"), "role": rtype.get("beskrivelse")}
            person, unit = role.get("person"), role.get("enhet")
            if person:
                if person.get("erDoed"):
                    continue
                n = person.get("navn") or {}
                item.update(kind="person", name=_join([n.get("fornavn"), n.get("mellomnavn"), n.get("etternavn")]))
            elif unit:
                item.update(kind="organisation", name=_join(unit.get("navn")),
                            organisation_number=unit.get("organisasjonsnummer"))
            else:
                continue
            if not item.get("name"):
                continue
            found.append(dict(item, effective_date=changed))
    found.sort(key=lambda x: (ROLE_ORDER.index(x["role_code"]) if x["role_code"] in ROLE_ORDER else 99, x["name"]))
    if not found:
        env.claim("leadership_workplaces", "roles", None, [env.cite(snap, "no active role holders")], "not_available")
    for item in found:
        span = "%s: %s" % (item["role"], item["name"])
        env.claim("leadership_workplaces", "role", {k: v for k, v in item.items() if k != "effective_date"},
                  [env.cite(snap, span, "role")], effective_date=item["effective_date"])
    return found


def fetch_subunits(client, env: Envelope, limit: int = 200) -> list[dict]:
    url = "%s/enhetsregisteret/api/underenheter?overordnetEnhet=%s&size=%d" % (BRREG, env.orgnr, limit)
    r = client.get(url, accept="application/json")
    snap = env.snapshot(r, "official_registry", "brreg_underenheter_json")
    if not r.ok:
        state = state_of_status(r.status)
        env.claim("leadership_workplaces", "registered_workplaces", None, [env.cite(snap, "HTTP %s" % r.status)], state)
        if state != "not_available":
            env.error("official.subunits", r.error or "HTTP %s" % r.status, url)
        return []
    try:
        data = r.json()
    except ValueError as exc:
        env.error("official.subunits", "invalid JSON: %s" % exc, url)
        return []
    units = (data.get("_embedded") or {}).get("underenheter") or []
    total = _get(data, "page", "totalElements")
    out = []
    for u in units:
        if u.get("nedleggelsesdato"):
            continue
        n = u.get("naeringskode1") or {}
        item = {"organisation_number": u.get("organisasjonsnummer"), "name": u.get("navn"),
                "address": address_text(u.get("beliggenhetsadresse")),
                "municipality": (u.get("beliggenhetsadresse") or {}).get("kommune"),
                "industry": {"code": n.get("kode"), "label": n.get("beskrivelse")} if n.get("kode") else None,
                "employees": u.get("antallAnsatte") if u.get("harRegistrertAntallAnsatte") else None}
        if u.get("hjemmeside"):
            item["website"] = u.get("hjemmeside")
        out.append((item, u.get("oppstartsdato") or u.get("registreringsdatoEnhetsregisteret")))
    if not out:
        env.claim("leadership_workplaces", "registered_workplaces", None,
                  [env.cite(snap, "totalElements: %s" % total)], "not_available")
    for item, start in out:
        env.claim("leadership_workplaces", "registered_workplace", item,
                  [env.cite(snap, "%s %s, %s" % (item["organisation_number"], item["name"], item["address"]),
                            "underenhet")], effective_date=start)
    if total and total > len(units):
        env.claim("leadership_workplaces", "registered_workplace_count", total,
                  [env.cite(snap, "totalElements: %s" % total)], note="only the first %d are listed" % len(units))
    return [i for i, _ in out]


ACCOUNT_FIELDS = (
    ("revenue", ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter")),
    ("sales_revenue", ("resultatregnskapResultat", "driftsresultat", "driftsinntekter", "salgsinntekter")),
    ("operating_expenses", ("resultatregnskapResultat", "driftsresultat", "driftskostnad", "sumDriftskostnad")),
    ("payroll_costs", ("resultatregnskapResultat", "driftsresultat", "driftskostnad", "loennskostnad")),
    ("operating_result", ("resultatregnskapResultat", "driftsresultat", "driftsresultat")),
    ("net_financial_items", ("resultatregnskapResultat", "finansresultat", "nettoFinans")),
    ("profit_before_tax", ("resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad")),
    ("tax_expense", ("resultatregnskapResultat", "ordinaertResultatSkattekostnad")),
    ("net_result", ("resultatregnskapResultat", "aarsresultat")),
    ("total_assets", ("eiendeler", "sumEiendeler")),
    ("fixed_assets", ("eiendeler", "anleggsmidler", "sumAnleggsmidler")),
    ("current_assets", ("eiendeler", "omloepsmidler", "sumOmloepsmidler")),
    ("cash_and_bank", ("eiendeler", "sumBankinnskuddOgKontanter")),
    ("equity", ("egenkapitalGjeld", "egenkapital", "sumEgenkapital")),
    ("total_debt", ("egenkapitalGjeld", "gjeldOversikt", "sumGjeld")),
    ("short_term_debt", ("egenkapitalGjeld", "gjeldOversikt", "kortsiktigGjeld", "sumKortsiktigGjeld")),
    ("long_term_debt", ("egenkapitalGjeld", "gjeldOversikt", "langsiktigGjeld", "sumLangsiktigGjeld")),
)
ACCOUNT_TYPE_TEXT = {"selskap": "company accounts", "konsern": "group accounts"}


def accounts_order(rows: list) -> list:
    """Company accounts before group accounts, each newest period first (the API lists the oldest first)."""
    rows = sorted(rows, key=lambda a: str(_get(a, "regnskapsperiode", "tilDato") or ""), reverse=True)
    return sorted(rows, key=lambda a: (a.get("regnskapstype") or "SELSKAP").upper() != "SELSKAP")


def fetch_accounts(client, env: Envelope, latest_year: str | None = None) -> list[dict]:
    """Filed annual accounts the open API returns (currently the last three years; company and, when filed, group),
    newest first, the newest period of each type marked `latest`. `latest_year` is the register's
    sisteInnsendteAarsregnskap; when the accounts API does not yet return that year, the claim says so."""
    url = "%s/regnskapsregisteret/regnskap/%s" % (BRREG, env.orgnr)
    r = client.get(url, accept="application/json")
    snap = env.snapshot(r, "official_registry", "brreg_regnskap_json")
    if not r.ok:
        state = state_of_status(r.status)
        env.claim("annual_accounts", "annual_accounts", None, [env.cite(snap, "HTTP %s" % r.status)], state)
        if state != "not_available":
            env.error("official.accounts", r.error or "HTTP %s" % r.status, url)
        return []
    try:
        data = r.json()
    except ValueError as exc:
        env.error("official.accounts", "invalid JSON: %s" % exc, url)
        env.claim("annual_accounts", "annual_accounts", None, [env.cite(snap)], "failed")
        return []
    rows = accounts_order([a for a in (data if isinstance(data, list) else [data]) if isinstance(a, dict)])
    out, seen_type = [], set()
    for acc in rows:
        period = acc.get("regnskapsperiode") or {}
        p = "%s/%s" % (period.get("fraDato"), period.get("tilDato"))
        kind = (acc.get("regnskapstype") or "SELSKAP").lower()
        currency = acc.get("valuta")
        values = {}
        for name, path in ACCOUNT_FIELDS:
            v = _get(acc, *path)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                values[name] = v
        meta = {"accounts_type": kind, "currency": currency, "period_start": period.get("fraDato"),
                "period_end": period.get("tilDato"), "latest": kind not in seen_type,
                "not_audited": _get(acc, "revisjon", "ikkeRevidertAarsregnskap"),
                "audit_opt_out": _get(acc, "revisjon", "fravalgRevisjon"),
                "small_company_rules": _get(acc, "regnkapsprinsipper", "smaaForetak"),
                "liquidation_accounts": acc.get("avviklingsregnskap"), "filing_id": acc.get("journalnr")}
        note = None
        if meta["latest"] and kind == "selskap" and latest_year and str(period.get("tilDato") or "")[:4] < str(
                latest_year):
            note = ("the register lists accounts for %s as submitted; the open accounts API does not return them yet"
                    % latest_year)
        seen_type.add(kind)
        env.claim("annual_accounts", "accounts_period", meta,
                  [env.cite(snap, "regnskapsperiode %s, %s, %s, journalnr %s" % (p, kind, currency, acc.get(
                      "journalnr")), "%s.periode" % kind)], reporting_period=p, note=note)
        for name, v in values.items():
            env.claim("annual_accounts", "%s" % name, {"amount": v, "currency": currency, "accounts_type": kind},
                      [env.cite(snap, "%s: %s %s" % (name, v, currency), "%s %s.%s" % (kind, p, name))],
                      reporting_period=p)
        out.append(dict(meta, values=values))
    if not out:
        env.claim("annual_accounts", "annual_accounts", None, [env.cite(snap, "empty list")], "not_available")
    return out


# Register update stream: which changed field means which public event. Contact fields (e-mail, phone) are skipped.
EVENTS = (
    ("/navn", "legal name changed to {v}"),
    ("/organisasjonsform", "legal form changed"),
    ("/forretningsadresse", "business address changed"),
    ("/postadresse", "postal address changed"),
    ("/naeringskode1", "main industry classification changed"),
    ("/vedtektsfestetFormaal", "registered purpose changed"),
    ("/aktivitet", "registered activity changed"),
    ("/sisteInnsendteAarsregnskap", "annual accounts for {v} filed"),
    ("/kapital/belop", "share capital set to NOK {v}"),
    ("/vedtektsdato", "articles of association changed"),
    ("/hjemmeside", "website in the register changed"),
    ("/konkurs", "bankruptcy flag set to {v}"),
    ("/underAvvikling", "liquidation flag set to {v}"),
    ("/underTvangsavviklingEllerTvangsopplosning", "forced-dissolution flag set to {v}"),
    ("/registrertIMvaregisteret", "VAT registration set to {v}"),
    ("/registrertIForetaksregisteret", "business register entry set to {v}"),
    ("/overordnetEnhet", "parent unit changed"),
)


def utc_date(ts: str | None) -> str:
    """Calendar date in UTC. The update stream gives the same update sometimes in local time («…T00:01+02:00») and
    sometimes in UTC («…T22:01Z» the day before); cutting the string would make an event's date flip between runs."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except ValueError:
        return (ts or "")[:10]
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).strftime("%Y-%m-%d")


def _num(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    return "{:,.0f}".format(x).replace(",", " ") if x == int(x) else str(x)


def fetch_updates(client, env: Envelope, months: int = 13, limit: int = 10) -> dict:
    """Dated changes of the register entry over the last `months` months (Enhetsregisteret update stream with JSON
    patches): public events (filed accounts, capital, address, name, status ...) and the monthly employee counts."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=31 * months)).strftime("%Y-%m-%dT00:00:00.000Z")
    url = ("%s/enhetsregisteret/api/oppdateringer/enheter?dato=%s&organisasjonsnummer=%s&includeChanges=true&size=100"
           % (BRREG, since, env.orgnr))
    r = client.get(url, accept="application/json")
    snap = env.snapshot(r, "official_registry", "brreg_oppdateringer_json")
    if not r.ok:
        state = state_of_status(r.status)
        env.claim("hiring_activity", "register_events", None, [env.cite(snap, "HTTP %s" % r.status)], state)
        if state != "not_available":
            env.error("official.updates", r.error or "HTTP %s" % r.status, url)
        return {"events": [], "employees": []}
    try:
        body = r.json()
        rows = [(u, snap) for u in (body.get("_embedded") or {}).get("oppdaterteEnheter") or []]
        pages = int((body.get("page") or {}).get("totalPages") or 1)
        if pages > 1:                                  # oldest first: the newest updates sit on the last page
            r2 = client.get(url + "&page=%d" % (pages - 1), accept="application/json")
            if r2.ok:
                snap2 = env.snapshot(r2, "official_registry", "brreg_oppdateringer_json")
                rows += [(u, snap2) for u in (r2.json().get("_embedded") or {}).get("oppdaterteEnheter") or []]
    except (ValueError, AttributeError, TypeError) as exc:
        env.error("official.updates", "invalid JSON: %s" % exc, url)
        return {"events": [], "employees": []}
    events, staff = [], []
    for u, s in rows:
        date = utc_date(u.get("dato"))
        patch = u.get("endringer") or []
        seen = set()
        for op in patch:
            path, value = op.get("path") or "", op.get("value")
            if path == "/antallAnsatte":
                if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
                    staff.append((date, int(value), s))
                continue
            for prefix, text in EVENTS:
                if (path == prefix or path.startswith(prefix + "/")) and prefix not in seen:
                    seen.add(prefix)
                    v = _num(value) if prefix == "/kapital/belop" else value
                    events.append((date, text.format(v=v if v not in (None, "") else "a new value"), path,
                                   u.get("oppdateringsid"), s))
    for date, text, path, uid, s in sorted(events, key=lambda x: (x[0], x[3] or 0), reverse=True)[:limit]:
        env.claim("hiring_activity", "register_event", {"date": date, "event": text,
                                                         "source": "Brønnøysund Register Centre, register update"},
                  [env.cite(s, "%s %s" % (date, path), "oppdateringsid %s" % uid)],
                  effective_date=date, note="dated change of the official register entry")
    if staff:
        staff.sort(key=lambda x: x[0])
        series = [{"date": d, "employees": n} for d, n, _ in staff]
        env.claim("legal_identity", "registered_employees_history", series,
                  sorted({env.cite(s, "%s: antallAnsatte %s" % (d, n), "oppdatering antallAnsatte")
                          for d, n, s in (staff[0], staff[-1])}, key=lambda i: int(i.split("-")[1])),
                  effective_date=series[-1]["date"], note="monthly employee counts the register received from Nav's "
                  "employer register over the last %d months" % months)
    return {"events": events, "employees": staff}


def official(client, env: Envelope) -> dict:
    entity = fetch_entity(client, env)
    if entity is None:
        return {"entity": None, "roles": [], "subunits": [], "accounts": [], "updates": {}}
    return {"entity": entity, "roles": fetch_roles(client, env), "subunits": fetch_subunits(client, env),
            "accounts": fetch_accounts(client, env, entity.get("sisteInnsendteAarsregnskap")),
            "updates": fetch_updates(client, env)}
