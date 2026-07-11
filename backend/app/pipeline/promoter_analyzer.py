"""Promoter & management narrative facts for the report (names, experience,
board composition signals). Scoring itself lives in scoring.py
(promoter_quality + governance categories); this module supplies the humans-
and-history layer those rules can't see.
"""
from __future__ import annotations

import re


def analyze_promoters(pages: list[dict], sections: dict, entities: dict, issue: dict) -> dict:
    prom = sections.get("promoters") or {}
    mgmt = sections.get("management") or {}
    out: dict = {
        "names": [],
        "experience_claims": [],
        "board": {},
        "group_company_conflicts": False,
        "past_ventures_mentioned": False,
        "source_pages": {"promoters": prom.get("page_start"), "management": mgmt.get("page_start")},
    }

    ptext = prom.get("text", "")[:60000]
    # "X, aged Y years, is one of our Promoters" / "our Promoters are A, B and C"
    # [ \t]+ (not \s+) between name words: a name must not span lines, else the
    # chapter heading above it gets swallowed into the capture.
    for m in re.finditer(r"([A-Z][a-zA-Z.]+(?:[ \t]+[A-Z][a-zA-Z.]+){1,3})\s*(?:,\s*aged\s+\d+\s+years?)?\s*,?\s+(?:is|are)\s+(?:one\s+of\s+)?(?:our|the)\s+Promoters?", ptext):
        name = m.group(1).strip()
        if name not in out["names"] and len(out["names"]) < 8 and not re.search(r"Company|Limited", name):
            out["names"].append(name)
    m = re.search(r"[Oo]ur\s+Promoters?\s+(?:are|is)\s+([^.]{5,200})\.", ptext)
    if m and not out["names"]:
        candidates = re.split(r",\s*|\s+and\s+", m.group(1))
        out["names"] = [c.strip() for c in candidates if 3 < len(c.strip()) < 60][:8]

    for m in re.finditer(r"(?:experience|track record)\s+of\s+(?:over|more than|approximately|around)?\s*(\d{1,2})\s+years", ptext + mgmt.get("text", "")[:60000], re.I):
        yrs = int(m.group(1))
        if yrs >= 3:
            out["experience_claims"].append(yrs)
    out["experience_claims"] = sorted(set(out["experience_claims"]), reverse=True)[:6]

    mtext = mgmt.get("text", "")[:80000]
    independents = len(re.findall(r"independent\s+director", mtext, re.I))
    women = len(re.findall(r"woman\s+director", mtext, re.I))
    out["board"] = {"independent_director_mentions": independents,
                    "woman_director_mentions": women}

    conflict_text = (sections.get("group_companies") or {}).get("text", "") + ptext
    out["group_company_conflicts"] = bool(
        re.search(r"(?:conflict\s+of\s+interest|similar\s+(?:line\s+of\s+)?business|common\s+pursuits)", conflict_text, re.I))
    out["past_ventures_mentioned"] = bool(
        re.search(r"(?:previously\s+(?:founded|promoted)|erstwhile\s+(?:venture|company)|disassociat\w+)", ptext, re.I))

    out["pre_issue_pct"] = issue.get("pre_issue_promoter_pct")
    out["post_issue_pct"] = issue.get("post_issue_promoter_pct")
    out["pledging"] = entities.get("pledging", {})
    return out
