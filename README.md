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
  [--previous previous/envelopes.jsonl | --no-previous] [--run-id ID] [--workers 12] [--expected-count N]
  [--nav-days 200]
```

The batch file may be JSON Lines (`{"organisation_number": "923609016"}` per line), a JSON list or
`{"organisation_numbers": [...]}`, CSV with or without a header (the first nine-digit field counts), or plain digits
one per line. The run writes exactly one terminal envelope per distinct input number, in input order, a
machine-readable run report, and (with `--site`) a static profile site: search, filters, sorting by revenue, staff
or latest news, a comparison of two to four companies, and a page per company that opens with its key signals and
links every fact to its evidence, on desktop and mobile, from a plain folder without a server. A crashed run resumes
from `<output>.partial` when started again with the same `--run-id`.

Refresh: running the same command again is a refresh. The envelopes the earlier run left at `--output` are kept as
`snapshots/envelopes-<run id>.jsonl` next to it and serve as the previous run (or name one with `--previous`; switch
it off with `--no-previous`). Each envelope then lists material changes since that run and keeps facts that
disappeared, with their original evidence, under `history`. Re-running on unchanged sources gives no change. What is
not a change of the company is not reported as one:

- a fact whose source failed in this run (the website timed out, the accounts service was down) stays as a claim
  marked `carried_forward`, with its original evidence and retrieval time, next to the failed claim; the website
  verified in the previous run is always checked again, and when it cannot be read the website is `failed`, not
  `not_available`;
- facts that are new only because their source failed in the previous run, or because a newer agent version writes a
  field the previous one did not, are counted under `refresh.first_observed`, not as added;
- dated items that leave a sliding window (register events older than 13 months, a site's older posts, older
  accounts years) move to `history`, not to the changes; register update times are compared as UTC dates, because
  the register gives the same update sometimes in local time and sometimes in UTC;
- between two agent versions only the value fields both versions write are compared.

Tests: `PYTHONPATH=src python -m unittest discover -s tests`.

## Smoke test (27 October 2026)

`smoke-test/` holds the 100-company smoke test of this code (version 0.7.1). The batch (`smoke-test/batch.jsonl`) is
the starter kit's standard draw, `select_entry_batch.py` with its default seed: `random.Random(20260823).sample` of 100
rows of the public universe in file order (rebuilt in a few lines; the kit's code itself was not run). Both runs
started from a fresh clone in an empty virtual environment without pip:

```bash
PYTHONPATH=src python -m brierwise_signalpost run --organisations smoke-test/batch.jsonl \
  --output smoke-test/envelopes.jsonl --report smoke-test/run-1-report.json --site docs --expected-count 100
# the same command again, with --report smoke-test/run-2-refresh-report.json
```

- First run (`run-1-report.json`): 100 of 100 envelopes, all completed, in 395 s (the Nav index: all 9,348 readable
  active ads), 5.2 s per company at the median and 12.8 s at p95, 10,758 requests, no third-party cost, 6,870 claims.
  Identity, accounts, leadership and hiring are available for all 100. Website: 9 verified, 3 `ambiguous` (one
  redirects to another firm's site, one answers only over plain http without the registered address, leader or
  number, one does not name the company), 1 `failed` (self-signed certificate), 87 `not_available`. Two companies
  link their own profiles (5), four publish dated posts (20); none has an active Nav ad. 98 of 100 summaries carry a
  line of key signals.
- Refresh (`run-2-refresh-report.json`), the same command right afterwards: the first run's envelopes are kept under
  `smoke-test/snapshots/`, all 100 companies were matched with them, 0 material changes, nothing carried forward or
  first observed; 399 s, 10,737 requests. `smoke-test/envelopes.jsonl` is this run's output.
- The static site of the refresh run is in `docs/` and served at https://brierwise.github.io/brierwise-signalpost/
  (search, filters, sorting, comparison, evidence links; desktop and mobile).

Random companies are mostly small: 44,855 of the 411,160 companies (10.9 %) carry a website in the published universe
file. The evaluation below also uses Builderr's public 100-company sample, which is weighted towards larger companies.

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
| Dated register events, monthly employee counts | Enhetsregisteret update stream (`oppdateringer/enheter` with JSON patches, last 13 months) | NLOD 2.0 | anchor; contact fields in the patches are ignored |
| Annual accounts | Brønnøysund Regnskapsregisteret API (`regnskap/{orgnr}`: the filed accounts of the last three years, company and group) | NLOD 2.0 | anchor; every figure is copied as filed, with its currency (some groups file in USD or EUR), period and filing id; nothing is computed into a claim |
| Job ads | Nav job vacancy feed (pam-stilling-feed, public token fetched at run time): every ad active in the changes of the last 200 days is read in full once per run, in a background thread | Nav API terms: free use and republication, inactive ads removed | employer organisation number equals the company or one of its registered subunits (matched by number only, never by name) |
| Website, own profiles, careers, dated items | the company's own website (pages, JSON-LD, RSS/Atom, WordPress posts API, news listings), robots.txt respected | company-owned | see "Identity gates" below; otherwise unpublished, a failing register entry is `ambiguous` |

### Identity gates for a website

Candidates, in this order: the website in the company's register entry; employer homepages in the company's own Nav
ads (when the Nav index is ready before the company's website step, which in large batches holds for most companies);
the domain of the e-mail address in the company's register entry (domain only, mail and internet providers
skipped, the address itself is never stored or shown); websites registered for its workplaces (up to three); domains
built from the legal name (whole name, with «aa»/«oe», as an internationalised domain, with initials joined
(«P.E. GAARUD AS» on pe-gaarud.no) and without a leading word for the kind of body («SAMEIET ST OLAV» on st-olav.no);
shortened name or first word); and, only with an evaluator key, web-search results. A candidate is published only with one of these proofs,
looked for on the home page and up to three contact or about pages of the same site:

1. the organisation number in a register context («org.nr», «MVA», «foretaksregisteret» ...), plus the company name
   unless the company declared the site itself (confidence 0.99);
2. the company name plus the registered street address with house number and postcode, on the candidate's own domain;
   not for shortened-name guesses, which a group company or a namesake may own (0.9);
3. a site the company declared itself (register entry or own Nav ad) that stays on its domain and names the company
   (0.85);
4. a site the company listed in the register whose domain (or the domain it forwards to) spells the whole company name
   without legal form, such as «CSB INVEST AS» on csbinvest.net (0.8);
5. the domain of the company's registered e-mail address, when the site stays on it and names the company on its home
   page (0.8);
6. the company name plus the full name of its registered daily manager or chair of the board (from the role register;
   also matched without a middle name) on the site; only for the register's site, an own Nav ad's homepage, the
   e-mail domain, a whole-name domain or a search candidate, never for shortened-name guesses or workplace sites, and
   not when a checked page shows other companies' organisation numbers or when the site's title names another legal
   entity that the page prints with its legal form (a sister company's site at the same address) (0.85). This is the «legal name plus
   leadership» combination named in the challenge's playbook; the quoted span shows the name as the site prints it.

Gates 2 to 6 never accept a placeholder page (parked, for sale, under construction, switched off, or a registrar's
parking page that names the domain), a site that calls itself by another legal entity's name, or a sole
proprietorship (which needs its organisation number on the site).

The register gives a host, not a URL. When a host the company gave (register, own Nav ad, workplace, the domain of
its registered e-mail address, or the site verified in the previous run) does not resolve, the same domain with or
without «www.» is tried once; when any candidate's certificate is valid only for the other of these two names, that
name is tried; when a host the company gave refuses connections on the https port, it is read over plain http, and
then only gates 1, 2 and 6 (organisation number, registered address, registered leader) can publish it, because a
domain kept only over plain http is often a neglected or re-registered one. Certificate checks are never relaxed,
and the claim's note says at which address the site was read.
Parent, group, manager and brand sites that fail these gates stay unpublished; housing co-ownerships, for example,
often list their manager's site in the register, which is reported as `ambiguous` with the reason. A platform page (Facebook, Instagram, LinkedIn
...) given as the website in the register is published as a declared company profile without fetching the platform.
Quoted evidence spans are exact substrings of the page text and stop short of e-mail addresses and phone numbers.

- Person data is used only in the company-centric role context: name and function of officers as published by the
  register. Dates of birth, registered phone numbers and e-mail addresses are not read into claims. Contact persons
  in job ads are not used.
- Social profiles are published only when the verified company site links them (JSON-LD `sameAs`, or an outbound link
  on the home page or its first contact or about page whose account name contains a word of the company name or the
  site's domain label; a three-letter word counts only as the whole account name, so that «KaiHenningMelberg» is not
  taken for «KAI MARKETING AS»), at most two accounts per platform, the same account only once whatever scheme, host
  or case the link uses, LinkedIn pages in their canonical form `/company/<name>`; or when the company gives the
  profile as its website in the register. No platform is scraped.
- A careers page is the first link on the verified home page whose path names careers or jobs as a whole word
  («karriere», «jobb», «jobbe», «stillinger», «careers», «jobs» ...) or whose short navigation text does; «jobber»
  (works), «jobben» (the job, in a story), «bestillinger» (orders) and «ledige lokaler» (vacant premises) do not count.
  JSON-LD job postings on it are listed. Absence of Nav ads is stated only when the whole feed was read.
- Dated items from the verified site: JSON-LD articles on its news page, its RSS/Atom feed, the WordPress posts API,
  and news listings: a block (article, list item, or a card whose class names a news item, post or card) with
  exactly one date, its `<time datetime>` or the one full date printed in it, and a title (heading or title
  element, else the link text without dates) that links to another page of the same site. Category and tag links
  never count, blocks with two different dates are skipped, and on the home page, where offers and events carry
  dates too, only blocks with a `<time datetime>` or a link to a news-like path count. Up to five items per
  source, each URL once, dates within the last three years and at most a week ahead.
- Web search is optional: only when the evaluator supplies `BRAVE_SEARCH_API_KEY`, one query per company without a
  registered website (`"<name>" <town>`) proposes candidate domains; results are never evidence, a candidate passes
  the same identity gates as any other (organisation number, or name plus registered street address on its own
  domain).
  Declared cost estimate: 0.005 USD per query, reported per envelope and per run. Without the key the agent makes no
  search requests and costs nothing.
- No language model is used; synthesis is deterministic and cites claims. It opens with one line of key signals
  for someone who wants to apply, sell, partner or invest: the direction of revenue against the previous filed
  year, the operating result, equity as a share of total assets (for a parent company from its group accounts; both
  shares are computed from the filed figures and say so), staff and its trend in the register's monthly series,
  hiring, and the date of the latest own news. The sentences that follow give the details with their sources.
- Outbound URL policy: http and https only, ports 80 and 443, no credentials in URLs, every host (and every redirect
  target) must resolve to public addresses; per-host spacing (one request per second for company sites, 20 per second
  for the register, at most 50 per second and 20 at a time for Nav's feed), timeouts, a 3 MB size limit, a time
  budget per company and 25 minutes for the Nav index (after that it is marked partial and absence of ads is not
  claimed).
- Secrets: none. The Nav public token is fetched at run time and never written to disk.
- Third-party cost: 0 USD per run without a search key.

## Evaluation (local, 26 October 2026)

Two random samples of 100 companies each from the public universe (seeds 20261026 and 20261127), run with the
command above:

| | sample 1, v0.1.0 | sample 1, v0.2.0 | sample 2, v0.2.0 |
|---|---|---|---|
| envelopes | 100/100 | 100/100 | 100/100 |
| verified official websites | 8 | 13 | 10 |
| companies with own profiles / dated activity | 4 / 2 | 7 / 5 | 5 / 2 |
| wall time (workers) | 175 s (6) | 211 s (6) | 127 s (12) |

Every published website and its proof span was reviewed by hand. Two sites that passed a weaker gate (counted 14 and
11 before the review) were withdrawn by tightening the gates: a site the register listed for a transport company that presents itself as another legal
entity (which lists the same site), and a law firm's site listed by a lawyer's sole proprietorship. The remaining 23
show the organisation number (12), the company name with the registered street address (9) or are declared by the
company and name it on the home page (2). A rerun with `--previous` produced no false changes. Most companies in the
universe are holding, property and housing entities without a website of their own; for them the envelope says so
explicitly. The register update stream gave dated register events for 100 of 100 companies of sample 2 (at least the
filing of the 2025 accounts) and a monthly employee series for all 14 that report employees.

Scale test, 1,000 random companies (seed 20261028), same command: 1,000 of 1,000 envelopes in 1,021 s with 12 workers
(per company p50 10.4 s, p95 23.5 s, maximum 49 s), 10,084 requests. Verified websites 131, companies with own
profiles 67, dated register events 998, monthly employee series 141, careers pages 23, active Nav ads 2; the only
errors were the unavailable accounts service.

Nav ads by organisation number (v0.3.0). In a random sample of 400 active Nav ads (26 October 2026), 199 belonged to
companies of the universe, every one of them through a registered workplace (the subunit number), and only 105 showed a
display name that matches the legal or workplace name. v0.3.0 therefore reads every active ad once per run (406 feed
pages, 9,283 active ads, 9,121 of them with an employer number; about 7 minutes in a background thread, while the other
layers work) and matches by number only. In the 1,000-company scale sample, 4 companies have active ads, of which
v0.2.0 found 2; in sample 1, 2 instead of 1 (a kindergarten whose ads use a shorter name). The 100-company smoke test
of v0.3.0 took 421 s, nearly all of it the Nav index; its websites (13) and all other sections were unchanged. The
1,000-company scale test with v0.3.0 (same seed, `--previous` the v0.2.0 run) gave 1,000 of 1,000 envelopes in 921 s
with 12 workers (per company p50 10.0 s, p95 19.5 s; 434 hiring steps waited for the index and ran at the end),
19,702 requests, job ads at 4 companies; one registered website timed out in the TLS handshake and was reported as
removed, which is why a failed source now keeps the previous value instead.

Annual accounts and refresh (v0.4, 26 October 2026 afternoon, when Regnskapsregisteret answered again). The open
accounts API returns the filed accounts of the last three years, oldest first; v0.4 lists company accounts before
group accounts, newest first, marks the latest period and keeps the currency as filed (the largest groups file in
USD). Every account figure of the 100-company smoke test was compared with a fresh read of the API by a separate
script: 4,466 of 4,466 equal, none missing. 1,000-company scale test with v0.4.1 (same seed as above): 1,000 envelopes
in 470 s with 12 workers (per company p50 4.7 s, p95 13.6 s), 18,024 requests; accounts for 998 companies (two answered
HTTP 500 and are `failed`), verified websites 136, ambiguous register websites 31 (mostly sites of a manager or parent
company: housing co-ownerships listing their manager, property companies listing their group). The new website gates
added 7 verified sites (5 by the e-mail domain gate, 2 by the domain-spells-the-name gate), each reviewed by hand. Refresh against the v0.3.0 run of the same sample showed three kinds of
false change, all fixed in v0.4.2: accounts reported as added although the service had only been down before, register
events whose date flipped between local time and UTC, and a website reported as removed while it only failed to
answer. Running v0.4.2 twice on the same 37 companies gave no change; a website that timed out in the second run was
kept as `carried_forward`.

Independent cross-check of the websites: in the same 1,000-company sample, 13 companies have a Wikidata item with their
organisation number (P2333), 11 of them with an official website (P856). The agent verified 9 of these 11, all nine
the same site as Wikidata; the other two are sites the register lists for a foundation, which the agent leaves
`ambiguous` because the site does not name the foundation or forwards to another domain.

Comparison with Builderr's public 100-company product sample (builderr.ai/signalpost, read only, 26 October 2026).
The sample leans towards larger companies with websites: 67 of its 100 companies list a website in the 2025 snapshot,
against 10.9 % of the universe. Its profiles give a website for 97 companies. v0.4.3 verified 71 of them (70 the
same domain, one the same company's .no site instead of .com) and none that the sample lacks. Of the 26 others, 6 stay
`ambiguous` (manager, chain or franchise sites, and one group site that now shows non-browser clients only a
verification page), 4 could not be read (two TLS errors, two HTTP 403) and 16 have no website in the register today
(two had one in August, when the sample was made). For these the sample relies on its own discovery and on the legal
name appearing on the page, and marks several as needing review or uncertain; this agent does not treat the name
alone as proof (one of these sites shows another company's organisation number, another is a group's site). v0.5.0 adds name variants (joined initials, names without «Sameiet» or «Stiftelsen») and gate
6: 74 websites, the 3 new ones reviewed by hand (two show the organisation number, one names the company and its
daily manager). Own social profiles: the sample lists 87 approved accounts at 43 companies, v0.5.0 89 at 39 companies,
75 of them the same (a second run with the final profile rules below gave the same, except that one registered
site timed out and with it its three profiles). On a new random sample of 100 (seed 20261099), v0.5.0 verified 16 websites instead of 14; both new
ones pass gate 6 and were reviewed by hand (a film company whose site names it and its chair; a company whose register
website carries its brand, names its daily manager and uses the company's own e-mail domain). On the 1,000-company
scale sample (seed 20261028), v0.5.0 verified 144 websites instead of 136 in 462 s with 12 workers: 7 by gate 6 and one
that answered this time. Each new site was reviewed by hand; the review removed an eighth, whose title and footer name a
sister company at the same address, by the title rule of gate 6.

Dated items from news listings (v0.6.0). On Builderr's public sample, 73 companies have a verified website, but
v0.5.0 found dated items for only 15: it read 35 news pages, and 24 of them gave nothing because they carry neither
JSON-LD nor a feed. v0.6.0 also reads news listings and found dated items for 25 companies (117 items, at most five
per source); no company lost any. Every new item was reviewed by hand against its page. The review of a first
version found an offer with a departure date on a home page, a category link taken for the post, titles that
carried the category and the date (parts of a card had counted as cards of their own), and a container whose one
date belonged to another item than its first heading; the rules above remove all of these, and checked again on
the same pages they keep every correct item.

Version 0.7.0 (27 October 2026). Second addresses: on Builderr's sample the register lists www.02365.no for a taxi
company whose certificate is valid only for 02365.no; read there, it gives 75 verified websites instead of 74 and own
profiles at 40 companies instead of 39. In the 1,000-company scale sample (seed 20261028) two sites were added this
way (one whose «www.» name does not resolve, showing the organisation number; one whose certificate is valid only for
«www.», showing name and registered address), and four registered hosts that could not be read now get a clear
state (`ambiguous`): three answer only over plain http with a parking page or an empty directory listing (two of them
a registrar's «<domain> is parked» page, which the placeholder test did not know yet; gates 3 and 4 would have
published them, hence the stricter rule for plain http), and one shows another legal entity. Careers pages: a review of all 49 careers pages published in both samples found 11 that were not careers pages
(vacant premises or flats, customer orders, and news stories and other pages whose path or link text only contained a
part of a word such as «jobben» or «ledige»: a services page, a page for residents, an advertising page, a page about
strain at work); with the whole-word rule above, the 38 careers pages published for
the same companies are all careers pages (each checked), and one company now shows its jobs page instead of its
vacant-premises page. Facebook links to a page's tabs or its old «/pg/» form now count as the page itself, and links to
events, videos, photos and posts are not taken for accounts. Key signals: 98 of Builderr's 100 companies get the line;
every evidence id it cites exists in the envelope.

The Nav index needs 7 to 15 minutes, depending on Nav's response times (405 feed pages and about 9,500 active ads on 26
October 2026; 20 parallel reads, at most 50 per second).

Known gaps: sites whose TLS certificate chain cannot be verified are not read; parked and directory domains are
rejected; without a search key, companies whose website is neither in the register nor at a domain built from their
name or their registered e-mail domain stay without a website.

## Licence

MIT.
