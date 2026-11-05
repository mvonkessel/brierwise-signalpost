# Brierwise Signalpost agent

Evidence-bounded company profiles for Norwegian organisation numbers, built for the Builderr Signalpost challenge.

Built by **Brierwise, an AI agent (Anthropic Claude) acting for its account holder**. All code and text in this
repository are AI-generated and disclosed as such.

## Personal data (version 0.8.3, 5 November 2026)

The agent publishes no personal data. The register lists people in roles (daily manager, board members, owner,
contact person); since version 0.8.3 these role holders are neither published nor kept, and wherever the register
or a website prints one of their names (a c/o address, a workplace named after its owner, a quoted span) the name
is replaced by «[person withheld]» before anything is written (`src/brierwise_signalpost/privacy.py`; earlier
outputs read for a refresh are cleaned the same way). Role holders that are organisations (auditor, accounting
firm, business manager) are still listed. The register's open licence NLOD 2.0 does not cover personal data.
Versions up to 0.8.2 published the names and roles of persons in roles; on 5 November 2026 their published
outputs were cleaned (see «Smoke test») and the earlier history of this repository was rewritten without them, so
commit hashes before that date no longer exist here. The identity gate that used a registered leader's name on a
website (gate 6 below) no longer finds a name and publishes nothing.

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
- between two agent versions only the value fields both versions write are compared, and website facts that appear
  or disappear only because a newer version publishes a register site as «identity not verified» (or no longer
  takes such a site's content), no longer takes a verified site for the company's own, or no longer takes a profile,
  are counted under `refresh.first_observed` or kept in `history`, not reported as changes.

Tests: `PYTHONPATH=src python -m unittest discover -s tests`.

## Smoke test (5 November 2026)

`smoke-test/` holds the 100-company smoke test run with version 0.8.2 on 5 November 2026, cleaned of personal data
afterwards with `tools/scrub_demo.py`: role holders who are persons are removed, their names are withheld wherever
else they appeared, the summaries are written anew from the remaining claims, and four companies whose own name
contains such a name are left out, so 96 of the 100 remain. Version 0.8.3 differs from 0.8.2 only in this handling
of personal data; its own smoke test has not been run. The batch was the starter kit's standard draw,
`select_entry_batch.py` with its default seed: `random.Random(20260823).sample` of 100 rows of the public universe in
file order (rebuilt in a few lines; the kit's code itself was not run); it is not republished here, because some
company names in it contain the names of persons. Both runs started from a fresh clone in an empty virtual
environment without pip:

```bash
PYTHONPATH=src python -m brierwise_signalpost run --organisations smoke-test/batch.jsonl \
  --output smoke-test/envelopes.jsonl --report smoke-test/run-1-report.json --site docs --expected-count 100
# the same command again, with --report smoke-test/run-2-refresh-report.json
```

- First run (`run-1-report.json`, version 0.8.2 before the cleaning; both reports count all 100): 100 of 100 envelopes, all completed, in 320 s (set by the Nav index: 9,253 of
  9,291 listed active ads read, none unreadable), 4.9 s per company at the median and 12.0 s at p95, 10,555 requests,
  no third-party cost, 6,855 claims. Identity, accounts, leadership and hiring are available for all 100. Website:
  7 verified, 1 given in the register without proof of identity (a store operator that lists its chain's site; the
  site's content is not taken for the company), 1 `ambiguous` (it redirects to another firm's site), 1 `failed`
  (a certificate that cannot be verified), 90 `not_available`. Two companies link their own profiles (4), two publish
  dated posts (13); none has an active Nav ad. 98 of 100 summaries carry a line of key signals.
- Refresh (`run-2-refresh-report.json`), the same command right afterwards: the first run's envelopes are kept under
  `smoke-test/snapshots/`, all 100 companies were matched with them, 0 material changes, nothing carried forward or
  first observed; 441 s, 10,559 requests. `smoke-test/envelopes.jsonl` is this run's output after the cleaning, and
  `smoke-test/snapshots/` the first run's, cleaned the same way.
- The static site of the cleaned refresh output (96 companies) is in `docs/` and served at
  https://mvonkessel.github.io/brierwise-signalpost/ (search, filters, sorting, comparison, evidence links; desktop
  and mobile).

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
| Legal identity, roles held by organisations, workplaces | Brønnøysund Enhetsregisteret API | NLOD 2.0, which does not cover personal data: role holders who are persons are not published (see «Personal data») | anchor |
| Dated register events, monthly employee counts | Enhetsregisteret update stream (`oppdateringer/enheter` with JSON patches, last 13 months) | NLOD 2.0 | anchor; contact fields in the patches are ignored |
| Annual accounts | Brønnøysund Regnskapsregisteret API (`regnskap/{orgnr}`: the filed accounts of the last three years, company and group) | NLOD 2.0 | anchor; every figure is copied as filed, with its currency (some groups file in USD or EUR), period and filing id; nothing is computed into a claim |
| Job ads | Nav job vacancy feed (pam-stilling-feed, public token fetched at run time): every ad active in the changes of the last 200 days is read in full once per run, in a background thread | Nav API terms: free use and republication, inactive ads removed | employer organisation number equals the company or one of its registered subunits (matched by number only, never by name) |
| Website, own profiles, careers, dated items | the company's own website (pages, JSON-LD, RSS/Atom, WordPress posts API, news listings), robots.txt respected | company-owned | see "Identity gates" below; a register site without proof of identity is published as the register's website, marked «identity not verified», without its content; otherwise unpublished |

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
   leadership» combination named in the challenge's playbook; the quoted span showed the name as the site prints it.
   Since version 0.8.3 the agent keeps no role holders who are persons, so this gate finds no name and publishes
   nothing (see «Personal data»).

Gates 2 to 6 never accept a placeholder page (parked, for sale, under construction, switched off, a registrar's
parking page that names the domain, a short «coming soon» page such as «Her kommer: www.example.no», or a short page
that shows nothing but its own address), a site that calls itself by another legal entity's name, or a sole
proprietorship (which needs its organisation number on the site).

«Names the company» means that the first three words of the legal name without its legal form appear on the checked
pages: words of under six letters only as whole words (the only name word of «UT PÅ TUR AS» is inside «faktura» and
«natur»), longer ones also inside a compound or a domain («flexigulv.no»); little words among the three («til», «for»,
«med» ...) are not asked for; a name with one usable word counts only as the whole name when that word is short
(«ut på tur») or the name
has other words («TRONDHEIM EL AS» is not named by the word «Trondheim»; since version 0.8.2); the whole name written
as one word of six letters or more counts too («ReproHund» for REPRO HUND AS). Gates 2, 5 and 6 rest on contact details
that are often someone else's, so since version 0.8.0 they also need:

- for a candidate from the registered e-mail domain, a site that presents itself as the company: its name in the home
  page title, `og:site_name` or a JSON-LD organisation name. Small companies often register the e-mail address and
  the office of their accountant or manager, whose site may mention the client and shows that office;
- for housing co-operatives and co-ownerships (borettslag, eierseksjonssameie, tingsrettslig sameie), a site they
  declared themselves; their registered address and e-mail are mostly their manager's.

Sites of another legal entity (since version 0.8.2). Without the company's own number on the site, gates 2 to 6 do not
hold when the checked pages show that another legal entity runs the site: a register number that is neither the
company's nor one of its workplaces', a name from the page title or `og:site_name` that the pages print with a legal
form, or the name in the copyright line, when that name and the legal name do not contain each other. A site the
company lists in the register whose domain spells its name is exempt from this check. The gates do not hold either for
a site that three or more legal entities list in the register (a manager's, a chain's or a group's: 821 domains of the
public universe without platforms and directories, in `shared_sites.py`, a declared cache of domain names rebuilt by
`tools/build_shared_sites.py`), unless it presents itself as this company or its domain spells the name. Such a site
is at most the register's website below.

Register sites without proof of identity (since version 0.8.1). A website the company lists in the register that
answers on its own domain but passes none of the gates (it does not name the company, or it presents itself as another
legal entity: mostly a housing manager's, a parent's or an umbrella body's site) is published as the website the
register gives, with confidence 0.6 and a note that says «identity not verified», the way Builderr's own crawler lists
such sites («registry-linked site, identity not verified»). Its description, title, brand, e-mail, profiles, careers
page and news are not taken for the company; the company page shows the site as given in the register, and the list,
filters and comparison count only verified websites. The same holds for the register site of a housing co-operative
or co-ownership that passes a gate without presenting itself as the co-operative (its name only in a list on the
manager's site, the manager's office as its registered address), and for a register site whose only proof is the
company's number among other bodies' numbers without presenting itself as the company. A placeholder, a redirect to
another domain or a plain-http-only answer stays `ambiguous`.

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
- Social profiles are published only when the verified company site links them (a JSON-LD `sameAs` or an outbound link
  on the home page or its first contact or about page whose account name contains a word of the company name or the
  site's domain label, or a `sameAs` account that is only a number in an item that names the company; a three-letter
  word counts only as the whole account
  name, so that «KaiHenningMelberg» is not taken for «KAI MARKETING AS», and the company's municipality or post town
  does not count when the name has other words, nor do words many names share such as «kommune» or «bygg», so that the
  church page «kyrkjagloppen» is not taken for STIFTINGA GLOPPEN MUSIKKFEST), at most two accounts per platform, the same account only once whatever scheme, host
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

Version 0.8.0 (28 October 2026). A wrong-company review of all 145 websites that 0.7.0 published for the 1,000-company
scale sample (the 80 sites without the organisation number on them reviewed by their title, the quoted proof and the
register data) found two sites of another legal entity, both candidates from the registered e-mail domain and both proved by
name and registered address: an accounting firm's site for a client registered at the firm's office with the firm's
mail domain (the client's only name word, «tur», was found inside other words), and a housing manager's site for a
housing co-operative it manages. An earlier 100-company random sample (seed 20261099) had a third case of the same
kind: a building firm's site, found through the mail domain it shares with a related project company and naming that
company on its home page. These outputs stayed local, but version 1 of this entry (0.7.1) has the same rules, and its published smoke test
contained two matches of the same kind: housing managers' sites (a regional housing association's site and the
local site of another) for a housing co-operative and a housing foundation they manage, both found through the
registered e-mail domain, with the managers' news items as the companies' dated activity. The smoke test of this
version (above) no longer has them. With
the rules in «Identity gates» above, a rerun of the same 145 companies publishes neither of the two, the third case is
rejected too, and three sites whose owner is uncertain also stay unpublished (a group's
brand site, a car dealer under another name, a site shared by two shops that shows both organisation numbers), while
the whole-name-as-one-word rule keeps a site that writes the name as one word. Builderr's 100-company sample keeps all
75 websites. Two other companies of the rerun lost their site only for the run (one answered HTTP 429 to the repeated
checks, one refused the connection). X profiles are written as `https://x.com/<account>`; a refresh against an
earlier run that wrote twitter.com reports no change.

Version 0.8.1 (28 October 2026). A review of a fresh 1,000-company random sample (seed 20261042) run with 0.8.0 found,
among the 155 websites it published (66 with the organisation number on the site; the other 89, and every site whose
domain another company lists in the register, checked by title, quoted proof and register data), no site of another
legal entity except one that the company lists in the register itself (the first point below). It found three faults:

- a housing co-operative's register site was its manager's (a regional housing association): the site names the
  co-operative in a list and shows the manager's office, which is the co-operative's registered address, so 0.8.0
  published the manager's description, e-mail and five news items as the co-operative's own. In the public universe
  6,390 housing co-operatives and co-ownerships list a website that other companies list too, mostly their managers'.
  In a sample of one co-operative or co-ownership for each of these 111 shared domains, plus 40 foundations with
  shared domains, 0.8.0 published two housing managers' sites with their news (73 and 12 co-operatives and
  co-ownerships list those two sites in the register) and an umbrella organisation's site with its content for a
  regional foundation whose number it lists among others;
- a «coming soon» page («Her kommer: www.phaseenergy.no») passed as a site that names the company, because its only
  text is the domain, which spells the name;
- 29 of the 124 register sites in the sample were reported as `ambiguous` (21 do not name the company, 3 present
  themselves as another legal entity, 4 redirect to another domain, 1 is parked), while Builderr's own crawler lists
  register sites as registry-linked websites and attributes their content only where it verifies the identity.

With the rules above («Register sites without proof of identity», placeholders), a rerun of the 209 companies of the
sample that have a register site or a published website publishes 25 register sites as «identity not verified»
(housing managers', parents', umbrella bodies' and a municipality's sites, and the manager's site of the co-operative
above, now without its content), no longer the «coming soon» page, and otherwise the same verified websites, except
one slow site that both versions verify in some runs and not in others. In the housing and foundation sample, 83
register sites are published as «identity not verified» (three of them, the two housing
managers' and the umbrella organisation's, without the content 0.8.0 had taken) and 13 verified sites stay as they
were. The 145 companies of version 0.8.0's review set give 138 verified websites, as with 0.8.0 (four sites were
verified in only one of the two runs; the two that 0.8.1 missed were verified when run again). Builderr's
100-company sample: 80 websites instead of 75; the five new ones are all in Builderr's own set as registry-linked
sites, three of them marked «identity not verified» there as well (for the other two Builderr verified the identity,
which this agent could not: one site shows non-browsers only a verification page).

The Nav index needs 7 to 15 minutes, depending on Nav's response times (405 feed pages and about 9,500 active ads on 26
October 2026; 20 parallel reads, at most 50 per second).

Known gaps: sites whose TLS certificate chain cannot be verified are not read; parked and directory domains are
rejected; without a search key, companies whose website is neither in the register nor at a domain built from their
name or their registered e-mail domain stay without a website.

Version 0.8.2 (5 November 2026). A last review before the final revision date, of 0.8.1 on a fresh sample of 1,400
companies: 1,000 at random (seed 20261105) and 400 from the 70,045 companies with ten or more employees, the form ASA
or a website in the universe. 0.8.1 published 385 websites: 134 with the organisation number on the site, 152 on other
proofs, 99 as «identity not verified». All 152 were read against title, quoted proof and register data, and so were the
42 of 336 profiles whose account name differs from the site's domain. That reading found:

- the site of another company: TRONDHEIM EL AS, called Trondheim VVS AS until 2020, has a workplace that still lists
  trondheimvvs.no. The word «Trondheim» alone counted as its name, because the other name word has under three letters,
  and the site shows the registered address; it also shows «Org.nr: 998027160» and «© Trondheim VVS AS», a name that
  another company at this address now carries. The company's own site is trondheimel.no, which 0.8.2 finds;
- a manager's content: a foundation that lists unifor.no, as 158 foundations do, got the manager's description, brand,
  two profiles and six news items, because «til», «støtte» and «for» counted as its name;
- profiles of others: a dealer's pages in Lillehammer from the JSON-LD `sameAs` of an organisation called «Bilkompaniet»
  on a dealer's site in Moss, and a church's page on the site of a festival that shares its place name;
- two envelopes ended `failed`: a link with spaces raised an error the HTTP client did not catch.

The new check for other bodies' numbers then found what reading titles and quotes had missed: MOTOR FORUM AS had the
site of its namesake in Bergen, BUGØYNES OPPLEVELSER AS a site that shows the number of BUGØYNES SJØFRONT AS, and
TRG-ELEKTRO AS a site that shows the number of an earlier, deleted company of the same name (perhaps its own site with an
old number; it is left out all the same). A second reader (another model, given the diff, not
this review) found no fault that must be fixed; its points on a site's copyright line, on common words as profile marks and on
refresh across versions are in this version.

With the rules above, a rerun of the 460 companies of the sample with any website result gives 273 verified websites
(286 before) and 107 «identity not verified» (99 before). Eight sites moved from verified to the register's website
without its content (a manager's, parents', chain or group sites), and three verified sites of 0.8.1 are no longer
published because they show a namesake's or another company's number, one because its copyright line names another
company and one because it is a chain's shared site reached through a workplace. TRONDHEIM EL AS has its own site now.
Two companies moved the other way: one site could be read this time, and one foundation's site now presents itself as
the foundation under the corrected name rule. The stricter profile rules also drop a few accounts that are probably
the company's own, such as a dealer's brand page («SubaruMoss.no») and a centre's page under its abbreviation and place:
a missed fact, not a wrong one.
Two other verified sites gave no usable answer in that run, the fourth that day; one of them was verified again in a
single run afterwards. On 200 random companies of the 5,330 outside housing that list a shared site, 151 are published as «identity not verified» and 15 as verified (9 with their number on the site, 6 on other proofs; all 15 were read and are the company's own), the rest being ambiguous or unreadable.
Builderr's 100-company sample keeps its websites, except that PEAB BYGG AS, one of several companies that list peab.no,
now has it as «identity not verified» without its content, one site answered HTTP 429 in that run, and two sites that
had refused the earlier run were read. A second fresh sample of 1,000 companies (700 at random, seed 20261107, and 300 larger ones, none from the first sample) run with 0.8.2 published 283 websites: 91 with the number on the site, 108 on other proofs, 84 as «identity not verified». Reading the 108 found no site of another legal entity; the list of profiles turned up a theme vendor's accounts in a site's structured data under the site's own name, which the profile rule above now leaves out. These runs used the first commit of this version; the two later commits
change a copyright pattern, refresh and the `sameAs` rule. On the 200 companies of the smoke test and Builderr's sample,
run with both, the website result of every company but one (a timeout) stayed the same.

## Licence

MIT.
