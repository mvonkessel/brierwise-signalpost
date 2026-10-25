import copy
import unittest

from brierwise_signalpost.jobs import core_name, norm_name
from brierwise_signalpost.model import Envelope, valid_orgnr
from brierwise_signalpost.refresh import compare
from brierwise_signalpost.website import guess_domains, orgnr_patterns, useful_item


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
        self.assertIn("mjorud.no", guess_domains("MJØRUD AS"))
        self.assertFalse(useful_item("Hello world!", "2026-01-01"))


if __name__ == "__main__":
    unittest.main()
