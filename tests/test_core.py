import copy
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


if __name__ == "__main__":
    unittest.main()
