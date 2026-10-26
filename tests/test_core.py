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


if __name__ == "__main__":
    unittest.main()
