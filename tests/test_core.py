import copy
import json
import unittest

from brierwise_signalpost.jobs import core_name, norm_name
from brierwise_signalpost.model import Envelope, valid_orgnr
from brierwise_signalpost.refresh import compare
from brierwise_signalpost.website import address_keys, guess_domains, orgnr_patterns, scan_page, useful_item


def env_with(claims):
    return {"organisation_number": "923609016", "run": {"run_id": "r1", "completed_at": "2026-10-26T00:00:00Z"},
            "claims": claims, "evidence": [{"id": "ev-1", "source_url": "https://x", "source_class": "official_registry",
                                            "retrieved_at": "t", "content_sha256": "h"}]}


def claim(field, value, **kw):
    c = {"section": "legal_identity", "field": field, "value": value, "availability": "available",
         "evidence_ids": ["ev-1"], "confidence": 0.99}
    c.update(kw)
    return c


class Core(unittest.TestCase):
    def test_orgnr_checksum(self):
        self.assertTrue(valid_orgnr("923609016"))
        self.assertFalse(valid_orgnr("923609017"))
        self.assertFalse(valid_orgnr("12345678"))

    def test_absence_is_never_a_value(self):
        env = Envelope("923609016", {})
        c = env.claim("annual_accounts", "revenue", 0, [], "not_available")
        self.assertIsNone(c["value"])

    def test_refresh_idempotent(self):
        a = env_with([claim("legal_name", "EQUINOR ASA"), claim("role", {"role_code": "DAGL", "name": "X"})])
        changes, history = compare(a, copy.deepcopy(a))
        self.assertEqual(changes, [])
        self.assertEqual(history, [])

    def test_refresh_changes_and_history(self):
        a = env_with([claim("legal_name", "OLD AS"), claim("role", {"role_code": "DAGL", "name": "X"})])
        b = env_with([claim("legal_name", "NEW AS"), claim("role", {"role_code": "DAGL", "name": "Y"})])
        changes, history = compare(a, b)
        kinds = sorted((c["field"], c["change"]) for c in changes)
        self.assertEqual(kinds, [("legal_name", "changed"), ("role", "added"), ("role", "removed")])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["evidence"][0]["id"], "ev-1")

    def test_orgnr_on_page(self):
        p = orgnr_patterns("923609016")
        self.assertTrue(p.search("Org.nr. 923 609 016 MVA"))
        self.assertTrue(p.search("NO923609016MVA"))
        self.assertFalse(p.search("19236090161"))

    def test_names(self):
        self.assertEqual(core_name("PECUS AS AVD OSLO"), "pecus")
        self.assertEqual(norm_name("Mjørud AS"), "mjorud")
        self.assertIn(("mjorud.no", "full"), guess_domains("MJØRUD AS"))
        self.assertIn(("bisto.no", "short"), guess_domains("BISTO ENTREPRENØR AS"))
        self.assertNotIn(("holmestrand.no", "short"), guess_domains("HOLMESTRAND MEDISINSKE SENTER AS"))
        self.assertFalse(useful_item("Hello world!", "2026-01-01"))

    def test_other_entity_and_profiles(self):
        from brierwise_signalpost.website import Page, other_entity, profile_url
        p = Page("https://nhtrans.no/")
        p.feed('<html><head><meta property="og:site_name" content="Nortrans AS"></head><body>x</body></html>')
        self.assertEqual(other_entity(p, "NORDHORDLAND TRANSPORTKONTOR AS"), "Nortrans AS")
        self.assertIsNone(other_entity(p, "NORTRANS AS"))
        self.assertEqual(profile_url("https://www.facebook.com/profile.php?id=123&ref=x"),
                         "https://www.facebook.com/profile.php?id=123")
        self.assertEqual(profile_url("https://www.instagram.com/ellashage/?hl=nb"), "https://www.instagram.com/ellashage")

    def test_name_variants_and_leaders(self):
        from brierwise_signalpost.model import Envelope
        from brierwise_signalpost.website import leader_on_page, leaders_of, profile_url
        self.assertIn(("pe-gaarud.no", "full"), guess_domains("P.E. GAARUD AS"))
        self.assertIn(("st-olav.no", "full"), guess_domains("SAMEIET ST OLAV"))
        self.assertIn(("hammaren.no", "full"), guess_domains("STIFTELSEN HAMMAREN"))
        self.assertNotIn(("sameiet.no", "short"), guess_domains("SAMEIET ST OLAV"))
        self.assertNotIn(("stiftelsen.no", "short"), guess_domains("STIFTELSEN HAMMAREN"))
        self.assertEqual(guess_domains("MJØRUD AS")[0], ("mjorud.no", "full"))
        env = Envelope("923609016", {})
        env.claim("leadership_workplaces", "role", {"role_code": "DAGL", "role": "Daglig leder", "kind": "person",
                                                     "name": "Kari Anne Nordmann"}, [])
        env.claim("leadership_workplaces", "role", {"role_code": "MEDL", "role": "Styremedlem", "kind": "person",
                                                     "name": "Per Hansen"}, [])
        env.claim("leadership_workplaces", "role", {"role_code": "LEDE", "role": "Styrets leder",
                                                     "kind": "organisation", "name": "MOR AS"}, [])
        leaders = leaders_of(env)
        self.assertEqual(leaders, [("Kari Anne Nordmann", "DAGL")])
        hit = leader_on_page("Kontakt oss: daglig leder Kari Nordmann, tlf 22 33 44 55", leaders)
        self.assertEqual(hit[0], "DAGL")
        self.assertIn("Kari Nordmann", hit[1])
        self.assertNotIn("22 33", hit[1])
        self.assertIsNone(leader_on_page("Per Hansen er styremedlem; Kari Nordmannsen svarer", leaders))
        from brierwise_signalpost.website import Page, handle_matches, titled_entity, without_leaders
        pg = Page("https://www.wenaaskapital.no/")
        pg.feed("<html><head><title>Wenaas Eftf</title></head><body>Vi bygger varige verdier</body></html>")
        self.assertEqual(titled_entity(pg, "Om oss. Wenaas EFTF. AS | 6386 Måndalen", "WENAAS KAPITAL AS"),
                         "wenaas eftf")
        pg2 = Page("https://www.oclinholding.no/")
        pg2.feed("<html><head><title>Oclin Holding AS</title></head><body>x</body></html>")
        self.assertIsNone(titled_entity(pg2, "Oclin Holding AS, Oslo", "OCLIN HOLDING AS"))
        self.assertFalse(handle_matches("/kaihenningmelberg", {"kai", "marketing", "kaimarketing"}))
        self.assertTrue(handle_matches("/company/tgs_220114", {"tgs"}))
        self.assertTrue(handle_matches("/@tgs8281", {"tgs"}))
        self.assertTrue(handle_matches("/brolykkeislandshester", {"brolykke"}))
        rest = without_leaders("matthias steiner nilsen daglig leder, garasjeport1", [("Matthias Steiner Nilsen", "DAGL")])
        self.assertNotIn("steiner", rest)
        self.assertIn("garasjeport1", rest)
        self.assertEqual(profile_url("https://www.linkedin.com/company/ragasco-as/mycompany/"),
                         "https://www.linkedin.com/company/ragasco-as")

    def test_batch_formats(self):
        import json
        import os
        import tempfile
        from brierwise_signalpost.run import load_batch
        cases = {
            "a.jsonl": '{"organisation_number": "923609016"}\n{"organisation_number": 923609016}\n{"orgnr": "12345"}\n',
            "b.json": json.dumps({"organisation_numbers": ["923609016", "920209270"]}),
            "c.json": json.dumps([{"organisation_number": "923609016"}, "920 209 270"]),
            "d.csv": "organisation_number,name\n923609016,EQUINOR ASA\n920209270,BISTO\n",
            "e.txt": "923609016\n\n920209270\n",
        }
        want = {"a.jsonl": (["923609016", "12345"], 1), "b.json": (["923609016", "920209270"], 0),
                "c.json": (["923609016", "920209270"], 0), "d.csv": (["923609016", "920209270"], 0),
                "e.txt": (["923609016", "920209270"], 0)}
        with tempfile.TemporaryDirectory() as d:
            for name, body in cases.items():
                p = os.path.join(d, name)
                with open(p, "w", encoding="utf-8") as fh:
                    fh.write(body)
                self.assertEqual(load_batch(p), want[name], name)

    def test_identity_proofs(self):
        entity = {"forretningsadresse": {"adresse": ["Storgata 12B"], "postnummer": "0155"}}
        keys = address_keys(entity)
        page = "Besøk oss i Storgata 12 B, 0155 Oslo. Tlf 22 33 44 55. Org.nr: 920 209 270 MVA"
        self.assertEqual(scan_page(page, orgnr_patterns("920209270"), keys)[0], "orgnr")
        kind, span = scan_page(page[:40], orgnr_patterns("920209270"), keys)
        self.assertEqual(kind, "address")
        self.assertIn(span, page)                      # an exact quote ...
        self.assertNotIn("22 33", span)                # ... that leaves the phone number out
        self.assertEqual(scan_page("Storgata 12 B, 0999 Oslo", orgnr_patterns("920209270"), keys)[0], None)
        self.assertEqual(scan_page("920 209 270", orgnr_patterns("920209270"), keys)[0], None)
        group = "Selskaper: Bisto AS org.nr 923 609 016. Bisto Entreprenør AS org.nr 920 209 270."
        self.assertEqual(scan_page(group, orgnr_patterns("920209270"), keys)[0], "orgnr_shared")

    def test_nav_ads_by_workplace_number(self):
        import json
        from brierwise_signalpost.http import Response
        from brierwise_signalpost.jobs import NavIndex, jobs

        def ad(uuid, orgnr, published):
            body = {"uuid": uuid, "status": "ACTIVE", "ad_content": {
                "uuid": uuid, "title": "Lærling", "published": published + "T08:00:00",
                "employer": {"name": "Kiwi Sentrum", "orgnr": orgnr, "homepage": "https://kiwi.no"},
                "workLocations": [{"city": "SKIEN", "municipal": "SKIEN"}]}}
            url = "https://pam-stilling-feed.nav.no/api/v1/feedentry/" + uuid
            return Response(url, url, 200, {}, json.dumps(body).encode(), "2026-10-26T06:00:00Z")

        index = NavIndex(None)
        index.status, index.read, index.pages = "loaded", 3, 1
        index.by_orgnr = {"974185411": [ad("u1", "974185411", "2026-10-01"), ad("u2", "974185411", "2026-10-20")],
                          "999999999": [ad("u3", "999999999", "2026-10-21")]}
        index._ready.set()
        env = Envelope("923609016", {})
        got = jobs(None, env, index, {"navn": "NG KIWI AS"}, [{"organisation_number": "974185411"}])
        self.assertEqual(got["jobs"], 2)
        posts = [c for c in env.claims if c["field"] == "job_posting"]
        self.assertEqual([c["value"]["published"] for c in posts], ["2026-10-20", "2026-10-01"])
        self.assertEqual(posts[0]["value"]["nav_ad_url"], "https://arbeidsplassen.nav.no/stillinger/stilling/u2")
        empty = Envelope("920209270", {})
        jobs(None, empty, index, {"navn": "BISTO AS"}, [])
        self.assertEqual([c["availability"] for c in empty.claims], ["not_available"])
        index.status = "partial"                       # absence is not claimed from a partly read feed
        part = Envelope("920209270", {})
        jobs(None, part, index, {"navn": "BISTO AS"}, [])
        self.assertEqual([c["availability"] for c in part.claims], ["failed"])

    def test_refresh_keeps_facts_whose_source_failed(self):
        from brierwise_signalpost.refresh import carry_forward
        site = claim("official_website", "https://davvi.no/", section="website_profiles")
        prof = claim("company_profile", {"platform": "facebook", "url": "https://www.facebook.com/davvi.no"},
                     section="website_profiles")
        a = env_with([claim("legal_name", "DAVVI AS"), site, prof])
        b = env_with([claim("legal_name", "DAVVI AS"),
                      claim("official_website", None, section="website_profiles", availability="failed")])
        carried = []
        changes, history = compare(a, b, carried)
        self.assertEqual(changes, [])                  # an unreachable site is not a removed website
        self.assertEqual(sorted(c["claim"]["field"] for c in carried), ["company_profile", "official_website"])
        self.assertEqual(carry_forward(b, carried), 2)
        kept = [c for c in b["claims"] if c.get("carried_forward")]
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(i in {e["id"] for e in b["evidence"]} for c in kept for i in c["evidence_ids"]))
        gone = env_with([claim("legal_name", "DAVVI AS"),
                         claim("official_website", None, section="website_profiles", availability="not_available")])
        changes, _ = compare(a, gone, [])
        self.assertEqual(sorted(c["change"] for c in changes), ["removed", "removed"])

    def test_refresh_ignores_new_output_fields_across_versions(self):
        ad = {"title": "Tannlege", "published": "2026-09-29", "url": "https://arbeidsplassen.nav.no/stillinger/stilling/x"}
        a = env_with([claim("job_posting", ad, section="hiring_activity")])
        b = env_with([claim("job_posting", dict(ad, positions="1"), section="hiring_activity")])
        a["run"]["agent_version"], b["run"]["agent_version"] = "0.2.0", "0.3.0"
        self.assertEqual(compare(a, b)[0], [])
        b["run"]["agent_version"] = "0.2.0"
        self.assertEqual([c["change"] for c in compare(a, b)[0]], ["changed"])
        b["claims"][0]["value"]["published"] = "2026-10-01"
        b["run"]["agent_version"] = "0.3.0"
        self.assertEqual([c["change"] for c in compare(a, b)[0]], ["changed"])

    def test_fetch_state(self):
        from brierwise_signalpost.http import Response
        from brierwise_signalpost.website import fetch_state

        def r(status, error=""):
            return Response("https://x.no/", "https://x.no/", status, error=error)
        self.assertEqual(fetch_state(r(0, "network: <urlopen error [Errno 11001] getaddrinfo failed>"), None),
                         "not_available")
        self.assertEqual(fetch_state(r(0, "refused: host is not public"), None), "not_available")
        self.assertEqual(fetch_state(r(0, "network: <urlopen error _ssl.c:993: The handshake operation timed out>"),
                                     None), "failed")
        self.assertEqual(fetch_state(r(404), None), "not_available")
        self.assertEqual(fetch_state(r(403), None), "blocked")
        self.assertEqual(fetch_state(r(503), None), "failed")
        self.assertEqual(fetch_state(None, "robots.txt disallows"), "blocked")
        self.assertEqual(fetch_state(None, "time budget for this company used up"), "failed")

    def test_clip(self):
        from brierwise_signalpost.synthesis import clip
        self.assertEqual(clip("Kort  tekst.\n"), "Kort tekst.")
        long = "Vi leverer trelast. " * 20
        self.assertTrue(clip(long).endswith("trelast."))
        self.assertLessEqual(len(clip(long)), 240)
        words = "ord " * 100
        self.assertTrue(clip(words).endswith(" …"))
        self.assertNotIn("or …", clip(words))

    def test_accounts_newest_company_first_with_currency(self):
        from brierwise_signalpost.http import Response
        from brierwise_signalpost.official import fetch_accounts
        from brierwise_signalpost.site import latest_revenue
        from brierwise_signalpost.synthesis import synthesize

        def row(kind, year, revenue, currency="USD"):
            return {"regnskapstype": kind, "valuta": currency, "journalnr": "%s0001" % (year + 1),
                    "regnskapsperiode": {"fraDato": "%d-01-01" % year, "tilDato": "%d-12-31" % year},
                    "resultatregnskapResultat": {"aarsresultat": revenue / 10,
                                                 "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": revenue}}},
                    "egenkapitalGjeld": {"egenkapital": {"sumEgenkapital": -5.0}}}
        api = [row("KONSERN", 2023, 900.0), row("KONSERN", 2025, 1000.0), row("SELSKAP", 2023, 90.0),
               row("SELSKAP", 2024, 80.0), row("SELSKAP", 2025, 100.0)]          # the API lists the oldest first

        class Client:
            def get(self, url, accept=None):
                return Response(url, url, 200, body=json.dumps(api).encode(), retrieved_at="2026-10-26T00:00:00Z")
        env = Envelope("923609016", {})
        out = fetch_accounts(Client(), env, "2026")
        self.assertEqual([(a["accounts_type"], a["period_end"], a["latest"]) for a in out],
                         [("selskap", "2025-12-31", True), ("selskap", "2024-12-31", False),
                          ("selskap", "2023-12-31", False), ("konsern", "2025-12-31", True),
                          ("konsern", "2023-12-31", False)])
        self.assertIn("2026", env.claims[0]["note"])  # the register lists 2026 as submitted, the API does not yet
        rev = latest_revenue(env.claims)
        self.assertEqual((rev["value"]["amount"], rev["value"]["currency"], rev["reporting_period"][-10:]),
                         (100.0, "USD", "2025-12-31"))
        text = " ".join(s["text"] for s in synthesize(env)["sentences"] if s["topic"] == "accounts")
        self.assertIn("Latest filed company accounts (2025-01-01 to 2025-12-31)", text)
        self.assertIn("revenue USD 100 (2024: USD 80, up 25%)", text)
        self.assertIn("Equity is negative", text)
        self.assertIn("group accounts as parent company (2025-01-01 to 2025-12-31): revenue USD 1 thousand", text)
        self.assertNotIn("NOK", text)

    def test_declared_domain_and_placeholders(self):
        from brierwise_signalpost.website import Page, domain_is_name, name_tokens, placeholder
        self.assertTrue(domain_is_name("www.csbinvest.net", "CSB INVEST AS"))
        self.assertTrue(domain_is_name("arena-trondheim.no", "ARENA TRONDHEIM AS"))
        self.assertTrue(domain_is_name("mjoerud.no", "MJØRUD AS"))
        self.assertFalse(domain_is_name("midtnorsk.jazzinorge.no", "MIDTNORSK AS"))   # a subdomain of a platform
        self.assertFalse(domain_is_name("obos.no", "SØRBYEN BORETTSLAG"))
        self.assertFalse(domain_is_name("abc.no", "ABC AS"))                          # too short to be telling
        self.assertEqual(name_tokens("STIFTELSEN MIDTNORSK JAZZSENTER"), ["midtnorsk", "jazzsenter"])
        self.assertEqual(name_tokens("SØRBYEN BORETTSLAG"), ["sorbyen", "borettslag"])
        page = Page("https://skinup.no/")
        page.feed("<html><head><title>Nettsiden er for øyeblikket utilgjengelig!</title></head><body></body></html>")
        self.assertTrue(placeholder(page))
        page = Page("https://x.no/")
        page.feed("<html><head><title>Rørlegger i Oslo</title></head><body>Vi er under oppbygging av nye tjenester? "
                  "Nei: vi leverer rør.</body></html>")
        self.assertTrue(placeholder(page))
        page = Page("https://y.no/")
        page.feed("<html><head><title>Snekker Hansen</title></head><body>Vi bygger hus.</body></html>")
        self.assertFalse(placeholder(page))

    def test_refresh_first_observed_and_sliding_windows(self):
        rev = claim("revenue", {"amount": 5.0, "currency": "NOK", "accounts_type": "selskap"}, section="annual_accounts",
                    reporting_period="2025-01-01/2025-12-31")
        ev_old = claim("register_event", {"date": "2025-09-01", "event": "business address changed"},
                       section="hiring_activity")
        ev_new = claim("register_event", {"date": "2026-10-01", "event": "annual accounts for 2025 filed"},
                       section="hiring_activity")
        a = env_with([claim("legal_name", "X AS"), claim("annual_accounts", None, section="annual_accounts",
                                                         availability="failed"), ev_old])
        b = env_with([claim("legal_name", "X AS"), rev, ev_new])
        observed = []
        changes, history = compare(a, b, [], observed)
        self.assertEqual([(c["field"], c["change"]) for c in changes], [("register_event", "added")])
        self.assertEqual([o["field"] for o in observed], ["revenue"])           # the accounts service was down before
        self.assertEqual([h["claim"]["field"] for h in history], ["register_event"])  # out of the window, not removed
        a["run"]["agent_version"], b["run"]["agent_version"] = "0.3.0", "0.4.0"
        b["claims"].append(claim("payroll_costs", {"amount": 1.0, "currency": "NOK", "accounts_type": "selskap"},
                                 section="annual_accounts", reporting_period="2025-01-01/2025-12-31"))
        observed = []
        compare(a, b, [], observed)
        self.assertEqual(sorted(o["field"] for o in observed), ["payroll_costs", "revenue"])

    def test_update_dates_in_utc(self):
        from brierwise_signalpost.official import utc_date
        self.assertEqual(utc_date("2026-07-01T00:01:06.025+02:00"), utc_date("2026-06-30T22:01:06.025Z"))
        self.assertEqual(utc_date("2025-11-13"), "2025-11-13")

    def test_keep_snapshot(self):
        import os
        import tempfile
        from brierwise_signalpost.__main__ import keep_snapshot
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "envelopes.jsonl")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"organisation_number": "923609016", "run": {"run_id": "run-1"}}) + "\n")
            copy = keep_snapshot(out)
            self.assertTrue(copy.endswith(os.path.join("snapshots", "envelopes-run-1.jsonl")))
            self.assertTrue(os.path.exists(copy))

    def test_refresh_new_accounts_year(self):
        def acc(field, year, amount):
            return claim(field, {"amount": amount, "currency": "NOK", "accounts_type": "selskap"},
                         section="annual_accounts", reporting_period="%d-01-01/%d-12-31" % (year, year))
        a = env_with([acc("revenue", 2023, 1.0), acc("revenue", 2024, 2.0), acc("payroll_costs", 2024, 3.0)])
        b = env_with([acc("revenue", 2024, 2.0), acc("revenue", 2025, 4.0), acc("payroll_costs", 2024, 3.5)])
        changes, history = compare(a, b)
        self.assertEqual(sorted((c["field"], c["change"], c["reporting_period"][:4]) for c in changes),
                         [("payroll_costs", "changed", "2024"), ("revenue", "added", "2025")])
        self.assertEqual(sorted(h["claim"]["reporting_period"][:4] for h in history), ["2023", "2024"])

    def test_news_listing_items(self):
        from datetime import date, timedelta
        from brierwise_signalpost.website import listing_items, text_dates
        d1, d2, d3 = (date.today() - timedelta(days=n) for n in (3, 40, 90))
        no = "%d. %s %d" % (d2.day, ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august", "september",
                                     "oktober", "november", "desember"][d2.month - 1], d2.year)
        html = """<html><body><nav><ul><li><a href="/om-oss">Om oss og vår historie</a> 01.01.2026</li></ul></nav>
        <div class="news-list">
          <article><h2><a href="/nyheter/ny-avdeling-i-bergen">Vi åpner ny avdeling i Bergen</a></h2>
            <time datetime="%s">x</time><p>Tekst</p></article>
          <div class="news-item"><h3>Ny daglig leder fra årsskiftet</h3><span>%s</span>
            <a href="/nyheter/ny-daglig-leder">Les mer</a></div>
          <li><a href="/nyheter/to-datoer">Arrangement med to datoer i samme blokk</a> %s og %s</li>
          <li><a href="/nyheter/">Alle nyheter</a> %s</li>
          <a class="card" href="/nyheter/sommerfest"><div class="card-body"><h3>Sommerfest for alle ansatte</h3>
            <time datetime="%sT10:00:00+02:00"></time></div></a>
          <li><a href="https://annen.no/sak">Sak på et annet nettsted om firmaet</a> %s</li>
        </div></body></html>""" % (d1.isoformat(), no, d1.strftime("%d.%m.%Y"), d2.strftime("%d.%m.%Y"),
                                   d1.strftime("%d.%m.%Y"), d3.isoformat(), d1.strftime("%d.%m.%Y"))
        env = Envelope("923609016", {})
        snap = {"source_url": "https://firma.no/nyheter", "source_class": "company_owned", "retrieved_at": "t",
                "content_sha256": "h", "extraction": "html"}
        self.assertEqual(listing_items(env, snap, html, "https://firma.no/nyheter"), 3)
        got = [(c["value"]["date"], c["value"]["title"], c["value"]["url"]) for c in env.claims]
        self.assertEqual(got, [(d1.isoformat(), "Vi åpner ny avdeling i Bergen",
                                "https://firma.no/nyheter/ny-avdeling-i-bergen"),
                               (d2.isoformat(), "Ny daglig leder fra årsskiftet",
                                "https://firma.no/nyheter/ny-daglig-leder"),
                               (d3.isoformat(), "Sommerfest for alle ansatte", "https://firma.no/nyheter/sommerfest")])
        self.assertEqual(listing_items(env, snap, html, "https://firma.no/nyheter"), 0)   # nothing twice
        self.assertEqual([d for d, _ in text_dates("12. okt. 2026, October 3, 2026, 2026-01-05, 31.02.2026")],
                         ["2026-10-12", "2026-10-03", "2026-01-05"])

    def test_news_listing_parts_tags_and_home_page(self):
        from datetime import date, timedelta
        from brierwise_signalpost.website import listing_items
        d = date.today() - timedelta(days=5)
        # parts of a card (title, meta, date) are not cards of their own; a category link is never the item
        blog = """<div class="blog-grid"><div class="blog-single columns">
          <a class="image" href="/post/hvilken-bilinnredning"></a>
          <div class="blog-single__title"><a href="/post/hvilken-bilinnredning">Hvilken bilinnredning er best?</a></div>
          <div class="blog-single__meta"><a href="/blog/tips_and_guides" class="b-category">Tips og Guider</a> %s</div>
        </div></div>""" % d.isoformat()
        # a card that is one link, with a tag and a short date in its text and the headline in a title element
        card = """<a class="knowledgehub-item" href="/info-hub/inside/zaptec-open"><div class="card">
          <div class="card-content"><div class="flex items-center"><span class="tag">Inside Zaptec</span>
          <time datetime="%s">%s</time></div><p class="card-title">Zaptec Open: a day on the course</p></div></div></a>
          """ % (d.isoformat(), d.strftime("%d.%m.%y"))
        tour = """<div class="tour-card"><a href="/yorkshire-dales/">Yorkshire Dales vandretur</a> %s</div>""" % \
            d.strftime("%d.%m.%Y")
        snap = {"source_url": "https://firma.no/", "source_class": "company_owned", "retrieved_at": "t",
                "content_sha256": "h", "extraction": "html"}
        env = Envelope("923609016", {})
        self.assertEqual(listing_items(env, snap, blog + card, "https://firma.no/blog"), 2)
        self.assertEqual(sorted(c["value"]["title"] for c in env.claims),
                         ["Hvilken bilinnredning er best?", "Zaptec Open: a day on the course"])
        env = Envelope("923609016", {})
        self.assertEqual(listing_items(env, snap, card + tour, "https://firma.no/", home=True), 1)   # no tour date
        self.assertEqual(listing_items(Envelope("923609016", {}), snap, tour, "https://firma.no/tilbud"), 1)
        # a container whose cards are not recognised as blocks: its one date belongs to one of two items, so no item
        many = """<section class="news"><a class="selected-news-extended" href="/nyheter/multiconsult">
          <h3>Multiconsult samler kontorene</h3><p>Tekst uten dato</p></a>
          <a class="selected-news-extended" href="/nyheter/coop"><h3>Coop Norge inngår leieavtale</h3>
          <p>(Oslo, %s) Coop Norge har inngått</p></a></section>""" % d.strftime("%d.%m.%Y")
        self.assertEqual(listing_items(Envelope("923609016", {}), snap, many, "https://firma.no/nyheter"), 0)


if __name__ == "__main__":
    unittest.main()
