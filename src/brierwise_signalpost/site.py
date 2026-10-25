"""Static, self-contained profile site: search and filter, one page per company with claim-level evidence, compare view.

No external scripts, fonts or trackers; works from a folder, on desktop and mobile.
"""
from __future__ import annotations

import html
import json
import os

CSS = """
:root{--bg:#fbfbf8;--fg:#1d1f21;--mut:#5d6166;--line:#dcdcd5;--card:#fff;--acc:#1f5f8b;--ok:#2f7d4f;--warn:#9a6b00;
--bad:#a33a2a;--chip:#eef1f4}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--fg:#e8e8e3;--mut:#a3a7ad;--line:#33373c;--card:#1d2024;
--acc:#7fb3dc;--ok:#7cc79a;--warn:#e0b24f;--bad:#ef8a7a;--chip:#262a2f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,
"Segoe UI",Roboto,sans-serif}a{color:var(--acc)}header{padding:16px;border-bottom:1px solid var(--line)}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:1.35rem;margin:0 0 4px}h2{font-size:1.1rem;
margin:24px 0 8px}.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:12px 14px;margin:10px 0}.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
input[type=search]{width:100%;padding:10px 12px;font-size:1rem;border:1px solid var(--line);border-radius:6px;
background:var(--card);color:var(--fg)}label.chip,.chip{display:inline-block;background:var(--chip);border-radius:999px;
padding:3px 10px;font-size:.85rem;margin:2px 4px 2px 0}.st-available{color:var(--ok)}.st-ambiguous,.st-blocked
{color:var(--warn)}.st-failed{color:var(--bad)}.st-not_available,.st-not_applicable{color:var(--mut)}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;
vertical-align:top}th{font-weight:600}sup a{text-decoration:none;font-size:.75rem}.ev{font-size:.85rem;
word-break:break-word}.list .item{display:grid;grid-template-columns:auto 1fr auto;gap:4px 12px;align-items:start}
.num{text-align:right;font-variant-numeric:tabular-nums}td.when{white-space:nowrap}button{padding:8px 14px;border-radius:6px;border:1px solid
var(--acc);background:var(--acc);color:#fff;font-size:.95rem}button:disabled{opacity:.45}
@media (max-width:640px){.hide-s{display:none}td,th{padding:5px 4px}}
"""

STATE_LABEL = {"available": "found", "not_available": "not found", "blocked": "blocked", "failed": "failed",
               "ambiguous": "ambiguous", "not_applicable": "not applicable"}
SECTION_TITLE = {"legal_identity": "Legal identity", "annual_accounts": "Annual accounts",
                 "leadership_workplaces": "Leadership and workplaces", "website_profiles": "Website and own profiles",
                 "hiring_activity": "Hiring and dated activity"}


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def summary(env: dict) -> dict:
    by = {}
    for c in env.get("claims", []):
        if c["availability"] == "available":
            by.setdefault(c["field"], []).append(c["value"])
    first = lambda f: (by.get(f) or [None])[0]
    form = first("organisation_form") or {}
    ind = (first("industry_codes") or [{}])[0]
    muni = first("municipality") or {}
    rev = first("revenue") or {}
    return {"o": env["organisation_number"], "n": first("legal_name") or env["organisation_number"],
            "f": form.get("code"), "m": muni.get("name"), "i": ind.get("label"), "ic": ind.get("code"),
            "r": rev.get("amount"), "e": first("registered_employees"), "w": first("official_website"),
            "j": len(by.get("job_posting", [])), "p": len(by.get("company_profile", [])),
            "s": first("registry_status"), "sec": env.get("sections", {})}


def value_text(v) -> str:
    if isinstance(v, dict):
        if "amount" in v:
            return "%s %s" % (format(v["amount"], ",.0f").replace(",", " "), v.get("currency") or "")
        if "platform" in v:
            return '<a href="%s" rel="nofollow noopener">%s</a>' % (e(v["url"]), e(v["platform"].title()))
        if "title" in v and ("published" in v or "date" in v):
            link = v.get("url")
            t = e(v["title"])
            return ('<a href="%s" rel="nofollow noopener">%s</a>' % (e(link), t) if link else t) + \
                " <span class=mut>%s</span>" % e(v.get("published") or v.get("date"))
        if "role" in v:
            return "%s: %s" % (e(v["role"]), e(v["name"]))
        return e(", ".join("%s: %s" % (k, x) for k, x in v.items() if x not in (None, "", [])))
    if isinstance(v, list):
        return e("; ".join(x.get("label") or x.get("name") or str(x) if isinstance(x, dict) else str(x) for x in v))
    if isinstance(v, str) and v.startswith("http"):
        return '<a href="%s" rel="nofollow noopener">%s</a>' % (e(v), e(v))
    return e(v)


def company_page(env: dict) -> str:
    s = summary(env)
    evs = {x["id"]: x for x in env.get("evidence", [])}
    refs = lambda ids: "".join('<sup><a href="#%s">[%s]</a></sup>' % (e(i), e(i.split("-")[1])) for i in ids)
    out = ['<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,'
           'initial-scale=1"><title>%s</title><style>%s</style></head><body>' % (e(s["n"]), CSS),
           '<header><a href="../index.html">← All companies</a><h1>%s</h1><div class=mut>Organisation number %s · %s'
           ' · %s</div><div class=row>%s</div></header><main>' % (
               e(s["n"]), e(s["o"]), e(s["f"] or ""), e((s["m"] or "").title()),
               "".join('<span class="chip st-%s">%s: %s</span>' % (st, e(SECTION_TITLE.get(k, k)),
                                                                   e(STATE_LABEL.get(st, st)))
                       for k, st in s["sec"].items()))]
    syn = (env.get("synthesis") or {}).get("sentences") or []
    if syn:
        out.append('<h2>Summary</h2><div class=card>')
        out += ["<p>%s%s</p>" % (e(x["text"]), refs(x["evidence_ids"])) for x in syn]
        out.append('<p class=mut>%s</p></div>' % e((env.get("synthesis") or {}).get("method", "")))
    for sec, title in SECTION_TITLE.items():
        rows = [c for c in env.get("claims", []) if c["section"] == sec]
        if not rows:
            continue
        out.append("<h2>%s</h2><div class=card><table>" % e(title))
        for c in rows:
            when = c.get("reporting_period") or c.get("effective_date") or ""
            val = value_text(c["value"]) if c["availability"] == "available" else \
                '<span class="st-%s">%s</span>' % (c["availability"], e(STATE_LABEL.get(c["availability"])))
            note = ' <span class=mut>%s</span>' % e(c["note"]) if c.get("note") else ""
            out.append('<tr><th>%s</th><td>%s%s%s</td><td class="mut hide-s when">%s</td></tr>' % (
                e(c["field"].replace("_", " ")), val, note, refs(c["evidence_ids"]), e(when)))
        out.append("</table></div>")
    if env.get("changes"):
        out.append("<h2>Changes since the previous run</h2><div class=card><table>")
        for ch in env["changes"]:
            out.append("<tr><th>%s</th><td>%s → %s</td><td>%s</td></tr>" % (
                e(ch.get("field")), e(ch.get("before")), e(ch.get("after")), refs(ch.get("evidence_ids", []))))
        out.append("</table></div>")
    out.append('<h2>Evidence</h2><div class="card ev"><ol>')
    for i, x in sorted(evs.items(), key=lambda kv: int(kv[0].split("-")[1])):
        out.append('<li id="%s" value="%s"><a href="%s" rel="nofollow noopener">%s</a> · %s · retrieved %s · '
                   'sha256 %s…%s</li>' % (e(i), e(i.split("-")[1]), e(x["source_url"]), e(x["source_url"]),
                                         e(x["source_class"]), e(x["retrieved_at"]), e((x.get("content_sha256") or
                                                                                        "")[:12]),
                                         ("<br><span class=mut>«%s»</span>" % e(x["claim_span"])) if x.get(
                                             "claim_span") else ""))
    out.append("</ol></div><p class=mut>Run %s · %s %s · generated by Brierwise, an AI agent; facts come only from "
               "the cited sources.</p></main></body></html>" % (e(env["run"].get("run_id")), e(env["run"].get(
                "agent")), e(env["run"].get("agent_version"))))
    return "".join(out)


INDEX_JS = """
const D=window.SIGNALPOST;const q=document.getElementById('q');const list=document.getElementById('list');
const sel=new Set();const cmp=document.getElementById('cmp');const nf=new Intl.NumberFormat('en');
const F={web:x=>!!x.w,jobs:x=>x.j>0,acc:x=>x.r!=null,active:x=>x.s==='active'};const on=new Set();
document.querySelectorAll('[data-f]').forEach(b=>b.addEventListener('change',ev=>{ev.target.checked?on.add(ev.target.
dataset.f):on.delete(ev.target.dataset.f);draw()}));
function draw(){const t=q.value.trim().toLowerCase();let n=0;const rows=[];for(const x of D){if(t&&!(x.n+' '+x.o+' '+
(x.m||'')+' '+(x.i||'')).toLowerCase().includes(t))continue;let ok=true;for(const f of on)if(!F[f](x))ok=false;
if(!ok)continue;if(++n>400)break;rows.push(`<div class="card item"><input type=checkbox aria-label="compare ${x.n}"
 ${sel.has(x.o)?'checked':''} onchange="tog('${x.o}',this.checked)"><div><a href="company/${x.o}.html">${esc(x.n)}</a>
<div class=mut>${x.o} · ${x.f||''} · ${esc((x.m||'').toLowerCase())} · ${esc(x.i||'')}</div></div><div class=num>${
x.r!=null?'NOK '+nf.format(Math.round(x.r/1e3))+'k':''}<div class=mut>${x.w?'website':''}${x.j?' · '+x.j+' jobs':''}
</div></div></div>`)}list.innerHTML=rows.join('')||'<p class=mut>No company matches.</p>';
document.getElementById('cnt').textContent=n>400?'first 400 shown':n+' shown'}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function tog(o,c){c?sel.add(o):sel.delete(o);cmp.disabled=sel.size<2||sel.size>4}
cmp.addEventListener('click',()=>location.href='compare.html#'+[...sel].join(','));q.addEventListener('input',draw);draw();
"""

COMPARE_JS = """
const D=window.SIGNALPOST;const ids=location.hash.slice(1).split(',');const X=D.filter(x=>ids.includes(x.o));
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
const url=u=>/^https?:\\/\\//i.test(u||'')?esc(u):'';
const nf=new Intl.NumberFormat('en');const rows=[['Organisation number',x=>esc(x.o)],['Legal form',x=>esc(x.f||'')],
['Municipality',x=>esc((x.m||'').toLowerCase())],['Industry',x=>esc(x.i||'')],['Status',x=>esc(x.s||'')],
['Revenue (latest)',x=>x.r!=null?'NOK '+nf.format(x.r):'not found'],['Registered employees',x=>esc(x.e??'not published')],
['Website',x=>url(x.w)?`<a href="${url(x.w)}" rel="nofollow noopener">${url(x.w)}</a>`:'not verified'],
['Own profiles',x=>esc(x.p)],['Active Nav job ads',x=>esc(x.j)]];
document.getElementById('t').innerHTML='<tr><th></th>'+X.map(x=>`<th><a href="company/${esc(x.o)}.html">${esc(x.n)}</a></th>`).
join('')+'</tr>'+rows.map(([k,f])=>'<tr><th>'+k+'</th>'+X.map(x=>'<td>'+f(x)+'</td>').join('')+'</tr>').join('');
"""


def build(envelopes_path: str, out_dir: str) -> int:
    os.makedirs(os.path.join(out_dir, "company"), exist_ok=True)
    data = []
    with open(envelopes_path, encoding="utf-8") as fh:
        for line in fh:
            env = json.loads(line)
            data.append(summary(env))
            with open(os.path.join(out_dir, "company", "%s.html" % env["organisation_number"]), "w",
                      encoding="utf-8") as out:
                out.write(company_page(env))
    data.sort(key=lambda x: str(x["n"]))
    with open(os.path.join(out_dir, "data.js"), "w", encoding="utf-8") as fh:
        fh.write("window.SIGNALPOST=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";")
    head = ('<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,'
            'initial-scale=1"><title>%s</title><style>%s</style></head><body>')
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(head % ("Signalpost company profiles", CSS) +
                 '<header><h1>Norwegian company profiles</h1><div class=mut>%d companies · every fact links to its '
                 'source · by Brierwise (AI agent)</div></header><main><input id=q type=search placeholder="Search '
                 'name, organisation number, municipality or industry" aria-label=Search><div class=row>'
                 '<label class=chip><input type=checkbox data-f=web> verified website</label><label class=chip>'
                 '<input type=checkbox data-f=jobs> hiring</label><label class=chip><input type=checkbox data-f=acc>'
                 ' accounts found</label><label class=chip><input type=checkbox data-f=active> active</label>'
                 '<span id=cnt class=mut></span><button id=cmp disabled>Compare selected</button></div>'
                 '<div id=list class=list></div></main><script src="data.js"></script><script>%s</script>'
                 '</body></html>' % (len(data), INDEX_JS))
    with open(os.path.join(out_dir, "compare.html"), "w", encoding="utf-8") as fh:
        fh.write(head % ("Compare companies", CSS) + '<header><a href="index.html">← All companies</a><h1>Compare'
                 '</h1></header><main><div class=card style="overflow-x:auto"><table id=t></table></div></main>'
                 '<script src="data.js"></script><script>%s</script></body></html>' % COMPARE_JS)
    return len(data)
