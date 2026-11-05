"""Remove personal data from envelopes written by agent versions up to 0.8.2, for a public demo (05.11.2026).

Those versions published role holders who are persons (names and roles from the register). For each envelope this
tool drops them, withholds their names wherever else they appear (privacy.scrub), and writes the synthesis anew from
the remaining claims, so no sentence names a person or a person's role. A company whose own legal or former name
contains such a name is left out of the demo altogether. `--extra-names` (a JSON list kept outside the repository)
adds names to look for; only names found in an envelope are used for it.

  PYTHONPATH=src python tools/scrub_demo.py OUT_DIR IN.jsonl [IN2.jsonl ...] [--extra-names NAMES.json]
"""
import argparse
import json
import os
import re
import sys
from types import SimpleNamespace

from brierwise_signalpost.privacy import MASK, person_names, scrub
from brierwise_signalpost.synthesis import synthesize, with_changes

WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def grams(text, n_max):
    ws = [w.lower() for w in WORD.findall(text)]
    for i in range(len(ws)):
        for n in range(2, n_max + 1):
            if i + n <= len(ws):
                yield " ".join(ws[i:i + n])


def name_index(names):
    idx = {}
    for name in names:
        ws = [w.lower() for w in WORD.findall(name)]
        if len(ws) >= 2:
            idx.setdefault(" ".join(ws), name)
            idx.setdefault(ws[0] + " " + ws[-1], name)
    return idx


def identity_named(env):
    for c in env.get("claims") or []:
        if c.get("field") in ("legal_name", "former_names") and MASK in json.dumps(c.get("value"), ensure_ascii=False):
            return True
    return False


def clean(env, extra_idx, n_max):
    names = person_names(env)
    if extra_idx:
        text = json.dumps(env, ensure_ascii=False)
        names |= {extra_idx[g] for g in grams(text, n_max) if g in extra_idx}
    withheld = scrub(env, names)
    if identity_named(env):
        return None, withheld
    synth = synthesize(SimpleNamespace(claims=env.get("claims") or []))
    ref = env.get("refresh") or {}
    prev = {"run": {"completed_at": ref.get("previous_completed_at")}} if ref.get("previous_run_id") else None
    env["synthesis"] = with_changes(synth, env.get("changes") or [], prev, env.get("claims") or [])
    if ref:
        ref["material_changes"] = len(env.get("changes") or [])
    return env, withheld


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--extra-names")
    a = ap.parse_args()
    extra_idx, n_max = {}, 2
    if a.extra_names:
        with open(a.extra_names, encoding="utf-8") as fh:
            extra_idx = name_index(json.load(fh))
        n_max = max((len(k.split()) for k in extra_idx), default=2)
    os.makedirs(a.out_dir, exist_ok=True)
    left_out, total = set(), 0
    cleaned = {}
    for path in a.inputs:
        rows = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    env, n = clean(json.loads(line), extra_idx, n_max)
                    total += n
                    if env is None:
                        left_out.add(json.loads(line)["organisation_number"])
                    else:
                        rows.append(env)
        cleaned[path] = rows
    for path, rows in cleaned.items():
        out = os.path.join(a.out_dir, os.path.basename(path))
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            for env in rows:
                if env["organisation_number"] not in left_out:
                    fh.write(json.dumps(env, ensure_ascii=False) + "\n")
    print(json.dumps({"files": len(cleaned), "left_out_companies": len(left_out), "withheld_occurrences": total}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
