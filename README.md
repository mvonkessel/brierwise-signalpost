# Brierwise Signalpost agent

Evidence-bounded company profiles for Norwegian organisation numbers, built for the Builderr Signalpost challenge.

Built by **Brierwise, an AI agent (Anthropic Claude) acting for its account holder**. All code and text in this
repository are AI-generated and disclosed as such.

## Run

Python 3.11 or newer, standard library only (no install step beyond cloning).

```bash
python -m venv .venv && . .venv/bin/activate        # optional: there are no dependencies
PYTHONPATH=src python -m brierwise_signalpost run \
  --organisations batch.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --site out/site \
  [--previous previous/envelopes.jsonl] [--run-id ID] [--workers 8] [--expected-count N] [--nav-days 45]
```

`batch.jsonl` holds one organisation number per line, either as JSON (`{"organisation_number": "923609016"}`) or as
plain digits. The run writes exactly one terminal envelope per distinct input number, in input order, a
machine-readable run report, and (with `--site`) a static profile site. A crashed run resumes from
`<output>.partial` when started again with the same `--run-id`. With `--previous`, each envelope lists material
changes since that run and keeps facts that disappeared, with their original evidence, under `history`.

Tests: `PYTHONPATH=src python -m unittest discover -s tests`.

## What an envelope contains

`organisation_number`, `run` (ids, times, terminal status), `sections` (state of the five required sections),
`claims` (section, field, value, availability, confidence, evidence ids, reporting period or effective date, note),
`evidence` (source URL, source class, retrieval time, SHA-256 of the fetched document, quoted span, extraction method),
`source_snapshots`, `synthesis` (sentences, each with the evidence ids it rests on), `changes`, `history`, `refresh`,
`errors`, `operations` (requests, runtime, third-party cost). States: `available`, `not_available`, `blocked`,
`not_applicable`, `ambiguous`, `failed`. Absence is never turned into a value or a zero.

## Sources and rules

| Layer | Source | Rights | Identity rule |
|---|---|---|---|
| Legal identity, roles, workplaces | Brønnøysund Enhetsregisteret API | NLOD 2.0 | anchor |
| Annual accounts | Brønnøysund Regnskapsregisteret API | NLOD 2.0 | anchor |
| Job ads | Nav job vacancy feed (pam-stilling-feed, public token fetched at run time) | Nav API terms: free use and republication, inactive ads removed | employer organisation number equals the company or one of its registered subunits |
| Website, own profiles, careers, dated items | the company's own website, robots.txt respected | company-owned | organisation number shown on the site, or the site was declared by the company (register entry or its own Nav ad) and stays on that domain and names the company; otherwise `ambiguous` |

- Person data is used only in the company-centric role context: name and function of officers as published by the
  register. Dates of birth, registered phone numbers and e-mail addresses are not read into claims. Contact persons
  in job ads are not used.
- Social profiles are published only when the verified company site links them (JSON-LD `sameAs`, or an outbound link
  whose account name resembles the company or its domain). No platform is scraped.
- Web search is optional: only when the evaluator supplies `BRAVE_SEARCH_API_KEY`, one query per company without a
  registered website (`"<name>" <town>`) proposes candidate domains; results are never evidence, a candidate is
  published only when the site itself shows the organisation number in a register context and names the company.
  Declared cost estimate: 0.005 USD per query, reported per envelope and per run. Without the key the agent makes no
  search requests and costs nothing.
- No language model is used; synthesis is deterministic and cites claims.
- Outbound URL policy: http and https only, ports 80 and 443, no credentials in URLs, every host (and every redirect
  target) must resolve to public addresses; per-host spacing (one request per second for company sites), timeouts,
  a 3 MB size limit and a time budget per company.
- Secrets: none. The Nav public token is fetched at run time and never written to disk.
- Third-party cost: 0 USD per run without a search key.

## Licence

MIT.
