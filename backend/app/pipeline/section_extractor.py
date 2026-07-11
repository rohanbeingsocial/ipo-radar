"""Stage 2: locate the canonical SEBI-ICDR chapters inside the document.

Priority: PDF bookmarks (TOC) -> printed table of contents -> heading scan.
Every section records which method found it (drives per-fact confidence).
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

# Canonical keys in typical RHP order, with synonyms (fuzzy TOC matching) and
# strict heading regexes (page-top scan). Order matters for span computation.
CANONICAL_SECTIONS: list[dict] = [
    {"key": "offer_summary", "synonyms": ["offer document summary", "summary of the offer document"],
     "heading": r"(?:OFFER DOCUMENT SUMMARY|SUMMARY OF THE OFFER DOCUMENT)"},
    {"key": "risk_factors", "synonyms": ["risk factors", "internal risk factors"],
     "heading": r"RISK FACTORS"},
    {"key": "general_information", "synonyms": ["general information"],
     "heading": r"GENERAL INFORMATION"},
    {"key": "capital_structure", "synonyms": ["capital structure"],
     "heading": r"CAPITAL STRUCTURE"},
    {"key": "objects_of_offer", "synonyms": ["objects of the offer", "objects of the issue"],
     "heading": r"OBJECTS OF THE (?:OFFER|ISSUE)"},
    {"key": "basis_for_offer_price", "synonyms": ["basis for offer price", "basis for issue price", "basis for the offer price"],
     "heading": r"BASIS FOR (?:THE )?(?:OFFER|ISSUE) PRICE"},
    {"key": "tax_benefits", "synonyms": ["statement of special tax benefits", "statement of tax benefits"],
     "heading": r"STATEMENT OF (?:SPECIAL )?TAX BENEFITS"},
    {"key": "industry_overview", "synonyms": ["industry overview", "our industry"],
     "heading": r"(?:INDUSTRY OVERVIEW|OUR INDUSTRY)"},
    {"key": "our_business", "synonyms": ["our business", "business overview"],
     "heading": r"(?:OUR BUSINESS|BUSINESS OVERVIEW)"},
    {"key": "key_regulations", "synonyms": ["key regulations and policies"],
     "heading": r"KEY REGULATIONS AND POLICIES"},
    {"key": "history_corporate", "synonyms": ["history and certain corporate matters", "history and corporate structure"],
     "heading": r"HISTORY AND (?:CERTAIN )?CORPORATE (?:MATTERS|STRUCTURE)"},
    {"key": "management", "synonyms": ["our management", "board of directors and management"],
     "heading": r"OUR MANAGEMENT"},
    {"key": "promoters", "synonyms": ["our promoters and promoter group", "our promoters", "promoters and promoter group"],
     "heading": r"(?:OUR )?PROMOTERS?(?: AND PROMOTER GROUP)?"},
    {"key": "group_companies", "synonyms": ["our group companies", "group companies"],
     "heading": r"(?:OUR )?GROUP COMPAN(?:Y|IES)"},
    {"key": "dividend_policy", "synonyms": ["dividend policy"],
     "heading": r"DIVIDEND POLICY"},
    {"key": "financial_statements", "synonyms": ["financial statements", "restated financial statements",
                                                 "financial information", "restated financial information"],
     "heading": r"(?:RESTATED )?(?:CONSOLIDATED )?FINANCIAL (?:STATEMENTS|INFORMATION)"},
    {"key": "other_financial_info", "synonyms": ["other financial information"],
     "heading": r"OTHER FINANCIAL INFORMATION"},
    {"key": "capitalisation", "synonyms": ["capitalisation statement", "capitalization statement"],
     "heading": r"CAPITALI[SZ]ATION STATEMENT"},
    {"key": "indebtedness", "synonyms": ["financial indebtedness"],
     "heading": r"FINANCIAL INDEBTEDNESS"},
    {"key": "mdna", "synonyms": ["management's discussion and analysis",
                                 "management discussion and analysis of financial condition"],
     "heading": r"MANAGEMENT'?S? DISCUSSION AND ANALYSIS"},
    {"key": "litigation", "synonyms": ["outstanding litigation and material developments", "outstanding litigations"],
     "heading": r"OUTSTANDING LITIGATIONS? AND MATERIAL DEVELOPMENTS?"},
    {"key": "government_approvals", "synonyms": ["government and other approvals"],
     "heading": r"GOVERNMENT AND OTHER APPROVALS"},
    {"key": "regulatory_disclosures", "synonyms": ["other regulatory and statutory disclosures"],
     "heading": r"OTHER REGULATORY AND STATUTORY DISCLOSURES"},
    {"key": "terms_of_offer", "synonyms": ["terms of the offer", "terms of the issue"],
     "heading": r"TERMS OF THE (?:OFFER|ISSUE)"},
    {"key": "offer_structure", "synonyms": ["offer structure", "issue structure"],
     "heading": r"(?:OFFER|ISSUE) STRUCTURE"},
    {"key": "offer_procedure", "synonyms": ["offer procedure", "issue procedure"],
     "heading": r"(?:OFFER|ISSUE) PROCEDURE"},
    {"key": "material_contracts", "synonyms": ["material contracts and documents for inspection"],
     "heading": r"MATERIAL CONTRACTS AND DOCUMENTS"},
]

FUZZY_THRESHOLD = 82


def _normalize(title: str) -> str:
    t = re.sub(r"section\s+[ivxlc]+\s*[-–:.]?\s*", "", title.strip().lower())
    return re.sub(r"[^a-z ]+", " ", t).strip()


def _match_canonical(title: str) -> tuple[str, float] | None:
    norm = _normalize(title)
    if not norm:
        return None
    best: tuple[str, float] | None = None
    for spec in CANONICAL_SECTIONS:
        for syn in spec["synonyms"]:
            score = fuzz.token_set_ratio(norm, syn)
            # token_set_ratio is permissive; require the first word too for short titles
            if score >= FUZZY_THRESHOLD and (best is None or score > best[1]):
                best = (spec["key"], score)
    return best


def _from_bookmarks(toc: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for entry in toc:
        if entry["level"] > 2 or entry["page"] < 1:
            continue
        m = _match_canonical(entry["title"])
        if m and (m[0] not in found or m[1] > found[m[0]]["_score"]):
            found[m[0]] = {"title": entry["title"].strip(), "page_start": entry["page"],
                           "method": "toc", "_score": m[1]}
    return found


def _from_printed_toc(pages: list[dict]) -> dict[str, dict]:
    """Parse 'RISK FACTORS ............ 28' style lines in the first pages."""
    found: dict[str, dict] = {}
    line_re = re.compile(r"^(.{4,80}?)[\s.·]{3,}(\d{1,4})\s*$")
    for page in pages[:15]:
        for line in page["text"].splitlines():
            m = line_re.match(line.strip())
            if not m:
                continue
            match = _match_canonical(m.group(1))
            page_no = int(m.group(2))
            if match and 1 <= page_no <= len(pages) and match[0] not in found:
                # printed page numbers can lag physical pages (roman prelims);
                # verify/adjust by scanning near the target for the heading
                found[match[0]] = {"title": m.group(1).strip(), "page_start": page_no,
                                   "method": "printed_toc", "_score": match[1]}
    return found


def _heading_on_page(page_text: str, heading_re: str) -> bool:
    pat = re.compile(rf"^\s*(?:SECTION\s+[IVXLC]+\s*[-–:.]?\s*)?{heading_re}\s*$", re.MULTILINE)
    head = "\n".join(page_text.splitlines()[:12])
    return bool(pat.search(head))


def _from_heading_scan(pages: list[dict], missing_keys: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    specs = [s for s in CANONICAL_SECTIONS if s["key"] in missing_keys]
    for page in pages:
        for spec in specs:
            if spec["key"] in found:
                continue
            if _heading_on_page(page["text"], spec["heading"]):
                found[spec["key"]] = {"title": spec["synonyms"][0].title(), "page_start": page["n"],
                                      "method": "heading_scan", "_score": 75}
    return found


def _verify_page(pages: list[dict], key: str, claimed: int) -> int:
    """Printed TOC page numbers often exclude roman-numeral prelim pages.
    Scan a window around the claimed page for the actual heading."""
    spec = next(s for s in CANONICAL_SECTIONS if s["key"] == key)
    for offset in range(0, 40):
        for candidate in {claimed + offset, claimed - offset}:
            if 1 <= candidate <= len(pages) and _heading_on_page(pages[candidate - 1]["text"], spec["heading"]):
                return candidate
    return claimed


def extract_sections(pages: list[dict], toc: list[dict]) -> dict[str, dict]:
    """Returns {key: {title, page_start, page_end, text, method, found}} for all
    canonical keys (found=False rows included so gaps are visible downstream)."""
    located = _from_bookmarks(toc)

    printed = _from_printed_toc(pages)
    for key, info in printed.items():
        if key not in located:
            info["page_start"] = _verify_page(pages, key, info["page_start"])
            located[key] = info

    missing = [s["key"] for s in CANONICAL_SECTIONS if s["key"] not in located]
    located.update(_from_heading_scan(pages, missing))

    # Compute spans: each section runs until the next located section starts.
    order = {s["key"]: i for i, s in enumerate(CANONICAL_SECTIONS)}
    starts = sorted(((info["page_start"], key) for key, info in located.items()),
                    key=lambda x: (x[0], order[x[1]]))
    result: dict[str, dict] = {}
    for i, (start, key) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(pages)
        end = max(start, end - 1 if i + 1 < len(starts) and starts[i + 1][0] > start else end)
        text = "\n".join(p["text"] for p in pages[start - 1:end])
        info = located[key]
        result[key] = {"title": info["title"], "page_start": start, "page_end": end,
                       "text": text, "method": info["method"], "found": True}

    for spec in CANONICAL_SECTIONS:
        if spec["key"] not in result:
            result[spec["key"]] = {"title": spec["synonyms"][0].title(), "page_start": None,
                                   "page_end": None, "text": "", "method": None, "found": False}
    return result


def section_hit_rate(sections: dict[str, dict]) -> float:
    core = ["risk_factors", "capital_structure", "objects_of_offer", "basis_for_offer_price",
            "industry_overview", "our_business", "promoters", "dividend_policy",
            "financial_statements", "litigation", "offer_structure", "mdna"]
    return sum(1 for k in core if sections.get(k, {}).get("found")) / len(core)
