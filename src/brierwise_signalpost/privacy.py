"""No personal data in published output (since version 0.8.3).

The register lists people in roles (daily manager, board members, owner, contact person). The agent publishes neither
these role holders nor their names anywhere else: inside one company's run their names serve only to withhold them
where the register or a website prints them (a c/o address, a workplace named after its owner, a quote), and they are
never written to an output. Role holders that are organisations (an auditor, an accounting firm, a parent company)
stay. Earlier outputs (versions up to 0.8.2 wrote role holders who are persons) are cleaned the same way before a
refresh compares or carries anything from them.
"""
from __future__ import annotations

import re

MASK = "[person withheld]"


def is_person_role(c) -> bool:
    v = c.get("value") if isinstance(c, dict) else None
    return isinstance(c, dict) and c.get("field") == "role" and isinstance(v, dict) and v.get("kind") == "person"


def _person_change(ch) -> bool:
    return isinstance(ch, dict) and ch.get("field") == "role" and any(
        isinstance(ch.get(k), dict) and ch[k].get("kind") == "person" for k in ("before", "after"))


def person_names(env: dict) -> set:
    """Names of role holders who are persons, as an earlier output wrote them (claims, history and changes)."""
    names = set()
    for c in env.get("claims") or []:
        if is_person_role(c) and c["value"].get("name"):
            names.add(c["value"]["name"])
    for h in env.get("history") or []:
        c = h.get("claim") if isinstance(h, dict) else None
        if is_person_role(c) and c["value"].get("name"):
            names.add(c["value"]["name"])
    for ch in env.get("changes") or []:
        if _person_change(ch):
            for k in ("before", "after"):
                if isinstance(ch.get(k), dict) and ch[k].get("name"):
                    names.add(ch[k]["name"])
    return names


def name_pattern(names):
    """Whole names only (two words or more; a long name also as first plus last word), in any case, with spaces,
    hyphens, underscores or dots between the words (as in a URL path)."""
    alts = set()
    for n in names:
        words = [w for w in re.split(r"[\s\-_.]+", (n or "").strip()) if w]
        if len(words) < 2:
            continue
        seqs = [words] + ([[words[0], words[-1]]] if len(words) > 2 else [])
        for seq in seqs:
            alts.add(r"[\s\-_.]+".join(re.escape(w) for w in seq))
    if not alts:
        return None
    return re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(sorted(alts, key=len, reverse=True)), re.IGNORECASE)


def _mask(obj, pat, count):
    if isinstance(obj, str):
        new, n = pat.subn(MASK, obj)
        count[0] += n
        return new
    if isinstance(obj, list):
        return [_mask(x, pat, count) for x in obj]
    if isinstance(obj, dict):
        return {k: _mask(v, pat, count) for k, v in obj.items()}
    return obj


def scrub(env: dict, names=None) -> int:
    """Remove role holders who are persons from an envelope (their claims, the evidence only they cite, history and
    change entries) and withhold every other occurrence of their names. `names` adds the names a run knows but did not
    write. Changes the envelope in place and returns the number of withheld occurrences."""
    names = set(names or ()) | person_names(env)
    dropped = set()
    keep = []
    for c in env.get("claims") or []:
        if is_person_role(c):
            dropped |= set(c.get("evidence_ids") or [])
        else:
            keep.append(c)
    if "claims" in env:
        env["claims"] = keep
    still = {i for c in keep for i in (c.get("evidence_ids") or [])}
    if "evidence" in env:
        env["evidence"] = [e for e in env["evidence"] if e.get("id") not in dropped or e.get("id") in still]
    if "history" in env:
        env["history"] = [h for h in env.get("history") or [] if not is_person_role(h.get("claim"))]
    if "changes" in env:
        env["changes"] = [ch for ch in env.get("changes") or [] if not _person_change(ch)]
    pat = name_pattern(names)
    if pat is None:
        return 0
    count = [0]
    for key in list(env):
        if key != "organisation_number":
            env[key] = _mask(env[key], pat, count)
    return count[0]
