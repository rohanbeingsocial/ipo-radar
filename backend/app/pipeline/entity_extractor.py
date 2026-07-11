"""Stage 4: issue details, peer table, objects of the offer, promoters,
litigation summary, RPT / contingent liabilities, dividend history.

Everything keeps a source page for citation.
"""
from __future__ import annotations

import re

import pdfplumber

from .financial_extractor import parse_number, UNIT_FACTORS

RUPEE = r"(?:₹|Rs\.?|INR)"
AMOUNT_RE = r"(?:₹|Rs\.?|INR)?\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs|crores?|cr|millions?|mn|billions?)?"
# Strict variant for note-level amounts: requires a currency marker so bare
# integers ("Ind AS 37", counts) can't be mistaken for amounts.
STRICT_AMOUNT_RE = r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|lacs|crores?|cr|millions?|mn|billions?)?"


def _to_crore(num: str, unit: str | None) -> float | None:
    val = parse_number(num)
    if val is None:
        return None
    factor = UNIT_FACTORS.get((unit or "crores").lower(), 1.0)
    return round(val * factor, 2)


def _search_pages(pages: list[dict], pattern: str, page_range: tuple[int, int] | None = None,
                  flags=re.I | re.S) -> tuple[re.Match, int] | None:
    lo, hi = page_range or (1, len(pages))
    for p in pages[lo - 1:hi]:
        m = re.search(pattern, p["text"], flags)
        if m:
            return m, p["n"]
    return None


def _sec_range(sections: dict, key: str) -> tuple[int, int] | None:
    sec = sections.get(key) or {}
    if sec.get("found"):
        return (sec["page_start"], sec["page_end"])
    return None


def extract_company_name(pages: list[dict]) -> str | None:
    # Cover page: the issuer name is printed in ALL CAPS; lead managers and
    # registrars also appear there but in title case ("Axis Capital Limited"),
    # so the all-caps form is tried first.
    head = pages[0]["text"] if pages else ""
    m = re.search(r"^([A-Z][A-Z0-9&.,()\- ]{3,80}LIMITED)\s*$", head, re.M) \
        or re.search(r"^([A-Z][A-Za-z0-9&.,()\- ]{3,80}(?:LIMITED|Limited))\s*$", head, re.M)
    return m.group(1).title().strip() if m else None


def extract_issue_details(pages: list[dict], sections: dict) -> dict:
    out: dict = {"source_pages": {}}
    cover_range = (1, min(8, len(pages)))
    ranges = [cover_range]
    for key in ("offer_structure", "terms_of_offer", "capital_structure", "general_information",
                "offer_summary", "basis_for_offer_price"):
        r = _sec_range(sections, key)
        if r:
            ranges.append(r)

    def find(pattern: str, name: str, flags=re.I | re.S):
        for r in ranges:
            hit = _search_pages(pages, pattern, r, flags)
            if hit:
                out["source_pages"][name] = hit[1]
                return hit[0]
        return None

    m = find(rf"price\s+band\s*[:of]*\s*{RUPEE}\s*([\d,\.]+)\s*(?:to|–|-)\s*{RUPEE}?\s*([\d,\.]+)", "price_band")
    if m:
        out["price_band_low"], out["price_band_high"] = parse_number(m.group(1)), parse_number(m.group(2))
    if out.get("price_band_low") is None:
        # fixed-price issues (common for SME prospectuses) print a single price
        m = find(rf"(?:issue|offer)\s+price\s*(?:of|:)?\s*{RUPEE}\s*([\d,]+(?:\.\d+)?)\s*(?:/-|per|each|\s)", "price_band")
        if m:
            out["price_band_low"] = out["price_band_high"] = parse_number(m.group(1))

    m = find(rf"face\s+value\s+of\s*{RUPEE}\s*([\d\.]+)", "face_value")
    if m:
        out["face_value"] = parse_number(m.group(1))

    m = find(rf"fresh\s+issue\s+of[^.]*?aggregating\s+(?:up\s+to\s+)?{AMOUNT_RE}", "fresh_issue")
    if m:
        out["fresh_issue_cr"] = _to_crore(m.group(1), m.group(2))

    m = find(rf"offer\s+for\s+sale\s+of[^.]*?aggregating\s+(?:up\s+to\s+)?{AMOUNT_RE}", "ofs")
    if m:
        out["ofs_cr"] = _to_crore(m.group(1), m.group(2))

    if out.get("fresh_issue_cr") is not None or out.get("ofs_cr") is not None:
        out["total_issue_cr"] = round((out.get("fresh_issue_cr") or 0) + (out.get("ofs_cr") or 0), 2)

    m = find(r"(?:bid\s+lot|lot\s+size|minimum\s+(?:bid|order)\s+(?:lot|quantity))\D{0,40}?(\d{1,4})\s+equity\s+shares", "lot_size")
    if m:
        out["lot_size"] = int(m.group(1))

    if find(r"\bNSE\b.{0,40}\bBSE\b|\bBSE\b.{0,40}\bNSE\b", "listing_at"):
        out["listing_at"] = "BSE, NSE"

    cap = _sec_range(sections, "capital_structure")
    m = _search_pages(pages, r"pre[\s-]?(?:offer|issue)[^%\n]{0,160}?promoters?[^%\n]{0,160}?([\d]{1,2}(?:\.\d+)?)\s*%", cap) \
        or _search_pages(pages, r"promoters?[^%\n]{0,160}?pre[\s-]?(?:offer|issue)[^%\n]{0,160}?([\d]{1,2}(?:\.\d+)?)\s*%", cap)
    if m:
        out["pre_issue_promoter_pct"] = parse_number(m[0].group(1))
        out["source_pages"]["pre_issue_promoter_pct"] = m[1]
    m = _search_pages(pages, r"post[\s-]?(?:offer|issue)[^%\n]{0,160}?promoters?[^%\n]{0,160}?([\d]{1,2}(?:\.\d+)?)\s*%", cap) \
        or _search_pages(pages, r"promoters?[^%\n]{0,160}?post[\s-]?(?:offer|issue)[^%\n]{0,160}?([\d]{1,2}(?:\.\d+)?)\s*%", cap)
    if m:
        out["post_issue_promoter_pct"] = parse_number(m[0].group(1))
        out["source_pages"]["post_issue_promoter_pct"] = m[1]
    return out


def extract_peers(pdf_path: str, pages: list[dict], sections: dict,
                  company_name: str | None = None) -> list[dict]:
    """The 'Basis for Offer Price' chapter mandatorily carries a listed-peer
    comparison table (name / EPS / P/E / RoNW / NAV). The issuer's own row is
    tagged is_issuer so peer statistics exclude it (its EPS still feeds the
    derived issue P/E for fixed-price documents)."""
    r = _sec_range(sections, "basis_for_offer_price")
    if not r:
        return []
    issuer_words = [w for w in re.split(r"\W+", (company_name or "").lower())
                    if w and w not in {"limited", "ltd", "private", "pvt"}]
    peers: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for n in range(r[0], min(r[1] + 1, r[0] + 15)):
            try:
                tables = pdf.pages[n - 1].extract_tables()
            except Exception:
                continue
            for table in tables or []:
                if not table or len(table) < 2:
                    continue
                header = " ".join(str(c or "") for c in table[0]).lower()
                if not (re.search(r"p\s*/\s*e|p/e ratio", header) and re.search(r"eps|earning", header)):
                    continue
                cols = [str(c or "").lower() for c in table[0]]

                def col_idx(pat: str) -> int | None:
                    for i, c in enumerate(cols):
                        if re.search(pat, c):
                            return i
                    return None

                i_pe, i_eps, i_ronw = col_idx(r"p\s*/?\s*e"), col_idx(r"eps|earning"), col_idx(r"ronw|return on net")
                for row in table[1:]:
                    name = str(row[0] or "").strip()
                    if not name or re.search(r"peer|company name|^name$|average|nifty", name, re.I):
                        continue
                    clean = re.sub(r"[#*^~†\s]+$", "", re.sub(r"\s+", " ", name))
                    peer = {"name": clean, "source_page": n}
                    if issuer_words and all(w in clean.lower() for w in issuer_words):
                        peer["is_issuer"] = True
                    if i_pe is not None and i_pe < len(row):
                        peer["pe"] = parse_number(row[i_pe])
                    if i_eps is not None and i_eps < len(row):
                        peer["eps"] = parse_number(row[i_eps])
                    if i_ronw is not None and i_ronw < len(row):
                        peer["ronw"] = parse_number(row[i_ronw])
                    if peer.get("pe") is not None or peer.get("eps") is not None:
                        peers.append(peer)
                if peers:
                    return peers
    return peers


def extract_issuer_pe(pages: list[dict], sections: dict) -> tuple[float | None, int | None]:
    """Issuer P/E at the price band, printed in Basis for Offer Price."""
    r = _sec_range(sections, "basis_for_offer_price")
    hit = _search_pages(pages, r"p\s*/\s*e\s*(?:ratio)?[^\n]{0,120}?(?:cap|upper|higher)[^\n]{0,80}?price\s+band[^\d]{0,40}([\d]{1,3}(?:\.\d+)?)", r) \
        or _search_pages(pages, r"price\s+band[^\n]{0,120}?p\s*/\s*e[^\d]{0,60}([\d]{1,3}(?:\.\d+)?)\s*times", r)
    if hit:
        return parse_number(hit[0].group(1)), hit[1]
    return None, None


OBJECT_CATEGORIES = [
    ("debt_repayment", r"repayment|prepayment|redemption.*debenture|borrowings"),
    ("capex", r"capital\s+expenditure|purchase\s+of\s+(?:machinery|equipment)|setting\s+up|expansion|construction|new\s+facility"),
    ("working_capital", r"working\s+capital"),
    ("acquisition", r"acquisition|inorganic|investment\s+in\s+subsidiar"),
    ("general_corporate", r"general\s+corporate\s+purposes?"),
]


def extract_objects(pages: list[dict], sections: dict) -> list[dict]:
    r = _sec_range(sections, "objects_of_offer")
    if not r:
        return []
    text = "\n".join(p["text"] for p in pages[r[0] - 1:min(r[1], r[0] + 9)])
    objects: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        for cat, pat in OBJECT_CATEGORIES:
            if re.search(pat, line, re.I):
                m = re.search(AMOUNT_RE, line)
                amount = _to_crore(m.group(1), m.group(2)) if m else None
                if not any(o["category"] == cat and o.get("amount_cr") == amount for o in objects):
                    objects.append({"purpose": line[:200], "category": cat,
                                    "amount_cr": amount, "source_page": r[0]})
                break
    return objects[:12]


def extract_litigation(pages: list[dict], sections: dict) -> dict:
    r = _sec_range(sections, "litigation")
    out = {"found": bool(r), "counts": {}, "total_amount_cr": None, "source_page": r[0] if r else None}
    if not r:
        return out
    text = "\n".join(p["text"] for p in pages[r[0] - 1:min(r[1], r[0] + 7)])
    for cat, pat in [("criminal", r"criminal\s+proceedings?\D{0,80}?(\d{1,4})"),
                     ("tax", r"tax\s+(?:proceedings?|matters?|litigations?)\D{0,80}?(\d{1,4})"),
                     ("statutory", r"(?:statutory|regulatory)\s+(?:proceedings?|actions?)\D{0,80}?(\d{1,4})"),
                     ("material_civil", r"material\s+civil\s+litigations?\D{0,80}?(\d{1,4})")]:
        m = re.search(pat, text, re.I)
        if m:
            out["counts"][cat] = int(m.group(1))
    m = re.search(rf"aggregate\s+amount[^.\n]{{0,120}}?{AMOUNT_RE}", text, re.I)
    if m:
        out["total_amount_cr"] = _to_crore(m.group(1), m.group(2))
    return out


def extract_rpt(pages: list[dict], sections: dict) -> dict:
    r = _sec_range(sections, "financial_statements")
    out = {"found": False, "total_cr": None, "source_page": None}
    if not r:
        return out
    for p in pages[r[0] - 1:r[1]]:
        if re.search(r"related\s+party\s+(?:transactions?|disclosures?)", p["text"][:2000], re.I):
            out["found"], out["source_page"] = True, p["n"]
            m = re.search(rf"(?:total|aggregate)[^\n]{{0,80}}related\s+party[^\n]{{0,80}}?{STRICT_AMOUNT_RE}", p["text"], re.I) \
                or re.search(rf"related\s+party\s+transactions?[^\n]{{0,120}}?(?:aggregat\w+|total\w*)[^\n]{{0,60}}?{STRICT_AMOUNT_RE}", p["text"], re.I)
            if m:
                out["total_cr"] = _to_crore(m.group(1), m.group(2))
            break
    return out


def extract_contingent_liabilities(pages: list[dict], sections: dict) -> dict:
    r = _sec_range(sections, "financial_statements")
    out = {"found": False, "total_cr": None, "source_page": None}
    if not r:
        return out
    for p in pages[r[0] - 1:r[1]]:
        m = re.search(rf"contingent\s+liabilit(?:y|ies)[^\n]{{0,200}}?{STRICT_AMOUNT_RE}", p["text"], re.I | re.S)
        if m:
            out["found"], out["source_page"] = True, p["n"]
            out["total_cr"] = _to_crore(m.group(1), m.group(2))
            break
    return out


def extract_dividend(pages: list[dict], sections: dict) -> dict:
    r = _sec_range(sections, "dividend_policy")
    out = {"found": bool(r), "declared": None, "source_page": r[0] if r else None}
    if not r:
        return out
    text = "\n".join(p["text"] for p in pages[r[0] - 1:r[1]])
    if re.search(r"(?:not\s+(?:declared|paid)|no\s+dividend)", text, re.I):
        out["declared"] = False
    elif re.search(r"dividend\s+of\s+₹|declared\s+(?:and\s+paid\s+)?dividends?", text, re.I):
        out["declared"] = True
    return out


def detect_pledging(pages: list[dict], sections: dict) -> dict:
    out = {"pledged": False, "evidence": None, "source_page": None}
    for key in ("capital_structure", "risk_factors"):
        r = _sec_range(sections, key)
        if not r:
            continue
        hit = _search_pages(pages, r"([^\n.]{0,150}pledg\w+[^\n.]{0,150}promot\w+[^\n.]{0,100}|[^\n.]{0,150}promot\w+[^\n.]{0,80}pledg\w+[^\n.]{0,150})", r)
        if hit:
            snippet = re.sub(r"\s+", " ", hit[0].group(1)).strip()
            if re.search(r"(?:none|nil|no|not)\s+(?:of\s+the\s+)?(?:equity\s+)?shares?\s+.{0,40}pledg|pledg\w+\s*:?\s*(?:nil|none)", snippet, re.I):
                continue
            out.update({"pledged": True, "evidence": snippet[:300], "source_page": hit[1]})
            break
    return out


def detect_auditor_flags(pages: list[dict], sections: dict) -> dict:
    r = _sec_range(sections, "financial_statements")
    out = {"qualified": False, "emphasis_of_matter": False, "source_page": None}
    if not r:
        return out
    for p in pages[r[0] - 1:min(r[1], r[0] + 24)]:
        if re.search(r"qualified\s+opinion|adverse\s+opinion|disclaimer\s+of\s+opinion", p["text"], re.I):
            out["qualified"], out["source_page"] = True, p["n"]
        if re.search(r"emphasis\s+of\s+matter", p["text"], re.I):
            out["emphasis_of_matter"] = True
            out["source_page"] = out["source_page"] or p["n"]
    return out
