import json
import os
import tempfile
import unittest

from brierwise_signalpost.http import Response
from brierwise_signalpost.model import Envelope
from brierwise_signalpost.official import fetch_roles
from brierwise_signalpost.privacy import MASK, scrub
from brierwise_signalpost.refresh import load_previous

# Placeholder names only (Ola and Kari Nordmann are the Norwegian «John and Jane Doe»).
ROLES = {"rollegrupper": [
    {"sistEndret": "2024-01-02", "roller": [
        {"type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
         "person": {"navn": {"fornavn": "Ola", "etternavn": "Nordmann"}, "erDoed": False}},
        {"type": {"kode": "LEDE", "beskrivelse": "Styrets leder"},
         "person": {"navn": {"fornavn": "Kari", "mellomnavn": "Anne", "etternavn": "Nordmann"}}},
        {"type": {"kode": "REVI", "beskrivelse": "Revisor"},
         "enhet": {"organisasjonsnummer": "987654321", "navn": ["REVISJON", "AS"]}}]}]}
ONLY_PERSONS = {"rollegrupper": [{"roller": [
    {"type": {"kode": "INNH", "beskrivelse": "Innehaver"}, "person": {"navn": {"fornavn": "Ola", "etternavn": "Nordmann"}}}]}]}


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, accept=None):
        return Response(url=url, final_url=url, status=200, body=json.dumps(self.payload).encode("utf-8"),
                        retrieved_at="2026-11-05T21:00:00Z")


def old_envelope():
    """An output as version 0.8.2 wrote it: role holders who are persons, and their names elsewhere."""
    return {"organisation_number": "923609016", "run": {"run_id": "r1", "agent_version": "0.8.2"},
            "claims": [
                {"section": "leadership_workplaces", "field": "role", "availability": "available",
                 "value": {"role_code": "DAGL", "role": "Daglig leder", "kind": "person", "name": "Ola Nordmann"},
                 "evidence_ids": ["ev-1"]},
                {"section": "leadership_workplaces", "field": "role", "availability": "available",
                 "value": {"role_code": "REVI", "role": "Revisor", "kind": "organisation", "name": "REVISJON AS",
                           "organisation_number": "987654321"}, "evidence_ids": ["ev-2"]},
                {"section": "legal_identity", "field": "postal_address", "availability": "available",
                 "value": {"address": ["C/O OLA NORDMANN", "Postboks 1"]}, "evidence_ids": ["ev-3"]}],
            "evidence": [{"id": "ev-1", "claim_span": "Daglig leder: Ola Nordmann"},
                         {"id": "ev-2", "claim_span": "Revisor: REVISJON AS"},
                         {"id": "ev-3", "claim_span": "C/O OLA NORDMANN"}],
            "synthesis": {"sentences": [{"topic": "leadership", "text": "Daily manager (CEO): Ola Nordmann.",
                                         "evidence_ids": ["ev-1"]}]},
            "history": [{"claim": {"field": "role", "value": {"kind": "person", "name": "Kari Nordmann",
                                                              "role_code": "LEDE"}}, "evidence": []},
                        {"claim": {"field": "official_website", "value": "https://www.example.no/ola-nordmann"},
                         "evidence": []}],
            "changes": [{"field": "role", "change": "removed", "before": {"kind": "person", "name": "Kari Nordmann"},
                         "after": None}]}


class Privacy(unittest.TestCase):
    def test_roles_keep_organisations_and_no_persons(self):
        env = Envelope("923609016", {})
        found = fetch_roles(FakeClient(ROLES), env)
        self.assertEqual([x["name"] for x in found], ["REVISJON AS"])
        roles = [c for c in env.claims if c["field"] == "role"]
        self.assertEqual([c["value"]["kind"] for c in roles], ["organisation"])
        self.assertEqual(env.person_names, {"Ola Nordmann", "Kari Anne Nordmann"})
        text = json.dumps(env.to_dict(), ensure_ascii=False)
        self.assertNotIn("Nordmann", text)

    def test_only_persons_is_not_called_no_role_holders(self):
        env = Envelope("923609016", {})
        self.assertEqual(fetch_roles(FakeClient(ONLY_PERSONS), env), [])
        state = [c for c in env.claims if c["field"] == "roles"][0]
        self.assertEqual(state["availability"], "not_available")
        span = env.evidence[0]["claim_span"]
        self.assertEqual(span, "role holders who are persons are not published")

    def test_scrub_removes_persons_everywhere(self):
        env = old_envelope()
        n = scrub(env)
        text = json.dumps(env, ensure_ascii=False)
        self.assertNotIn("nordmann", text.lower())
        self.assertEqual([c["value"]["kind"] for c in env["claims"] if c["field"] == "role"], ["organisation"])
        self.assertEqual([e["id"] for e in env["evidence"]], ["ev-2", "ev-3"])
        self.assertEqual(env["evidence"][1]["claim_span"], "C/O " + MASK)
        self.assertEqual(env["claims"][1]["value"]["address"][0], "C/O " + MASK)
        self.assertEqual(env["changes"], [])
        self.assertEqual(len(env["history"]), 1)
        self.assertTrue(env["history"][0]["claim"]["value"].endswith("/" + MASK))
        self.assertGreaterEqual(n, 3)

    def test_scrub_uses_names_the_run_did_not_write(self):
        env = {"organisation_number": "1", "claims": [], "evidence": [{"id": "ev-1", "claim_span": "Kontakt: Kari Nordmann"}]}
        self.assertEqual(scrub(env, {"Kari Anne Nordmann"}), 1)
        self.assertEqual(env["evidence"][0]["claim_span"], "Kontakt: " + MASK)

    def test_one_word_is_never_masked(self):
        env = {"organisation_number": "1", "claims": [], "evidence": [{"id": "ev-1", "claim_span": "Nordmann Bygg AS"}]}
        self.assertEqual(scrub(env, {"Nordmann"}), 0)
        self.assertEqual(env["evidence"][0]["claim_span"], "Nordmann Bygg AS")

    def test_previous_output_is_cleaned_before_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(old_envelope(), ensure_ascii=False) + "\n")
            prev = load_previous(path)
        self.assertNotIn("nordmann", json.dumps(prev, ensure_ascii=False).lower())


if __name__ == "__main__":
    unittest.main()
