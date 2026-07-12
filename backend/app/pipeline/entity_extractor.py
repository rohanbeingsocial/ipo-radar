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


def extract_issue_details(pages: list[dict], sections: dict,
                          pdf_path: str | None = None) -> dict:
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

    # The shareholding is a Pre-Offer | Post-Offer TABLE, so the prose regex above finds it
    # in ~2% of documents. Fall back to the table. Note a draft prints the post-offer column
    # as "[●]" (the final share count isn't known), so this can still legitimately come back
    # empty — but for a real RHP it is there, and it is the number that says how much of the
    # company the promoter is keeping.
    if out.get("post_issue_promoter_pct") is None and pdf_path and cap:
        got = _promoter_holding_from_tables(pdf_path, cap)
        if got:
            out["post_issue_promoter_pct"] = got[0]
            out["source_pages"]["post_issue_promoter_pct"] = got[1]
    return out


def _promoter_holding_from_tables(pdf_path: str, cap: tuple[int, int]) -> tuple[float, int] | None:
    """Post-offer promoter/promoter-group % from the capital-structure shareholding table."""
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception:                                    # noqa: BLE001
        return None
    with pdf:
        for n in range(cap[0], min(cap[1] + 1, cap[0] + 25)):
            try:
                tables = pdf.pages[n - 1].extract_tables() or []
            except Exception:                            # noqa: BLE001
                continue
            for table in tables:
                start, cols = _fold_header(table)
                if not cols:
                    continue
                header = " ".join(cols)
                if not re.search(r"post[\s-]?(?:offer|issue)", header):
                    continue
                # the % column on the POST side — take the last '%' column, since the table
                # runs [pre: shares, %] then [post: shares, %]
                pct_cols = [i for i, c in enumerate(cols) if re.search(r"%|percentage", c)]
                if not pct_cols:
                    continue
                i_pct = pct_cols[-1]
                for row in table[start:]:
                    label = str(row[0] or "")
                    if not re.search(r"promoter", label, re.I) or re.search(r"public|non[- ]promoter", label, re.I):
                        continue
                    if i_pct < len(row):
                        v = parse_number(row[i_pct])
                        if v is not None and 0 < v <= 100:
                            return v, n
    return None


_PE_PAT = r"p\s*[/\\]?\s*e\b|\bpe\s*ratio\b|price\s*(?:/|to)\s*earning"
_EPS_PAT = r"\beps\b|earning[s]?\s+per\s+share"


def _is_data_row(row) -> bool:
    """A peer row = a company name plus at least two numbers. Header fragments carry a
    name-ish word but no numbers, which is what separates them."""
    name = str(row[0] or "").strip()
    if len(name) < 3 or not re.search(r"[A-Za-z]", name):
        return False
    return sum(1 for c in row[1:] if parse_number(c) is not None) >= 2


def _fold_header(table) -> tuple[int, list[str]]:
    """Peer tables in real prospectuses wrap their column headers over SEVERAL physical
    rows, so table[0] alone is usually blank or a fragment:

        row0: ['', '', '', 'Closing price', '', '', '']
        row1: ['', 'Revenue', '', '', '', 'EPS', 'EPS']
        row2: ['', '', 'Face value', 'on', '', '', '']
        row3: ['Name of the', 'from', '', '', 'P/E', '(Basic) (₹', '(Diluted) (₹']

    Reading only row 0 finds neither 'P/E' nor 'EPS' and the table is discarded — which is
    why peer extraction was failing on ~95% of real documents while passing on tidy test
    PDFs. Fold every row above the first data row into one header string per COLUMN."""
    start = next((i for i, row in enumerate(table) if _is_data_row(row)), None)
    if not start:                      # no data rows, or data on row 0 with no header
        return 0, []
    ncols = max(len(r) for r in table)
    cols = []
    for c in range(ncols):
        cols.append(" ".join(str(table[r][c] or "") for r in range(start)
                             if c < len(table[r])).strip().lower())
    return start, cols


def extract_peers(pdf_path: str, pages: list[dict], sections: dict,
                  company_name: str | None = None) -> list[dict]:
    """The 'Basis for Offer Price' chapter mandatorily carries a listed-peer
    comparison table (name / EPS / P/E / RoNW / NAV). The issuer's own row is
    tagged is_issuer so peer statistics exclude it (its EPS still feeds the
    derived issue P/E, which matters because many documents print the issuer's own
    P/E as a '[●]' placeholder that is only filled at pricing)."""
    r = _sec_range(sections, "basis_for_offer_price")
    if not r:
        return []
    issuer_words = [w for w in re.split(r"\W+", (company_name or "").lower())
                    if w and w not in {"limited", "ltd", "private", "pvt"}]
    peers: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for n in range(r[0], min(r[1] + 1, r[0] + 30)):
            try:
                tables = pdf.pages[n - 1].extract_tables()
            except Exception:
                continue
            for table in tables or []:
                if not table or len(table) < 2:
                    continue
                start, cols = _fold_header(table)
                if not cols:
                    continue
                header = " ".join(cols)
                if not (re.search(_PE_PAT, header) and re.search(_EPS_PAT, header)):
                    continue

                def col_idx(pat: str) -> int | None:
                    return next((i for i, c in enumerate(cols) if re.search(pat, c)), None)

                # 'EPS (Basic)' and 'EPS (Diluted)' both appear; basic is the disclosed basis
                i_eps = col_idx(r"eps.*basic|basic.*eps") or col_idx(_EPS_PAT)
                i_pe, i_ronw = col_idx(_PE_PAT), col_idx(r"ronw|return on net")
                for row in table[start:]:
                    name = str(row[0] or "").strip()
                    if not name or re.search(r"peer|company name|^name$|average|nifty", name, re.I):
                        continue
                    clean = re.sub(r"[#*^~†\s]+$", "", re.sub(r"\s+", " ", name))
                    peer = {"name": clean, "source_page": n}
                    if issuer_words and all(w in clean.lower() for w in issuer_words):
                        peer["is_issuer"] = True
                    for key, i in (("pe", i_pe), ("eps", i_eps), ("ronw", i_ronw)):
                        if i is not None and i < len(row):
                            peer[key] = parse_number(row[i])
                    if peer.get("pe") is not None or peer.get("eps") is not None:
                        peers.append(peer)
                if peers:
                    return peers
    return peers


def extract_issuer_eps(pdf_path: str, sections: dict) -> float | None:
    """The issuer's weighted-average basic EPS, from the table SEBI mandates in
    'Basis for Offer Price':

        Particulars  | Basic EPS (₹) | Diluted EPS (₹) | Weight
        Fiscal 2026  |     9.68      |      9.31       |   3
        Fiscal 2025  |    11.03      |     10.81       |   2
        Fiscal 2024  |     8.32      |      8.32       |   1

    This is the canonical issuer EPS and we were not reading it. The old code could only
    get the issuer's EPS if the issuer also appeared as a row in the PEER table, which is
    a table about OTHER companies — it is often absent there (found in only 7 of 25 real
    documents). Without an issuer EPS there is no issue P/E, and with no issue P/E the
    whole valuation category (50 of 465 rubric points) never scores.
    """
    r = _sec_range(sections, "basis_for_offer_price")
    if not r:
        return None
    with pdfplumber.open(pdf_path) as pdf:
        for n in range(r[0], min(r[1] + 1, r[0] + 30)):
            try:
                tables = pdf.pages[n - 1].extract_tables()
            except Exception:
                continue
            for table in tables or []:
                if not table or len(table) < 2:
                    continue
                start, cols = _fold_header(table)
                if not cols:
                    continue
                header = " ".join(cols)
                # the EPS-by-year table: basic EPS and a weight column, and NO P/E column
                # (that one is the peer table, handled above)
                if not (re.search(r"\beps\b", header) and re.search(r"weight", header)):
                    continue
                i_eps = next((i for i, c in enumerate(cols)
                              if re.search(r"basic", c) and re.search(r"eps", c)), None)
                if i_eps is None:
                    i_eps = next((i for i, c in enumerate(cols) if re.search(r"\beps\b", c)), None)
                i_w = next((i for i, c in enumerate(cols) if re.search(r"weight", c)), None)
                if i_eps is None or i_w is None:
                    continue
                num, den = 0.0, 0.0
                for row in table[start:]:
                    label = str(row[0] or "").lower()
                    if "weighted" in label:          # the document's own average row
                        v = parse_number(row[i_eps]) if i_eps < len(row) else None
                        if v is not None and v != 0:
                            return round(v, 2)
                        continue
                    if not re.search(r"fiscal|financial year|fy\s*\d|20\d\d", label):
                        continue
                    eps = parse_number(row[i_eps]) if i_eps < len(row) else None
                    w = parse_number(row[i_w]) if i_w < len(row) else None
                    if eps is not None and w:
                        num += eps * w
                        den += w
                if den:
                    return round(num / den, 2)
    return None


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


_UNIT_RE = re.compile(r"(?:₹|rs\.?|inr)?\s*(?:in\s+)?(lakhs?|lacs|crores?|millions?|billions?)", re.I)


def _page_unit(text: str) -> str | None:
    """Financial notes state their unit once, in the table caption ('₹ in lakhs'), never in
    the cells. Reading a cell without it is how you turn ₹500 lakh into ₹500 crore."""
    m = _UNIT_RE.search(text[:1500]) or _UNIT_RE.search(text)
    return m.group(1).lower() if m else None


def _total_from_tables(pdf_path: str, page_no: int, label_re: str) -> float | None:
    """Pull the 'Total' row out of a table on a page, returning its first plausible number.

    Contingent liabilities and related-party aggregates live in TABLES, not in prose. The
    old extractors regexed the page text for '<phrase> ... ₹123 crore' on one line, which
    essentially never occurs in a restated financial note — which is exactly why both rules
    scored on 0% of documents."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            tables = pdf.pages[page_no - 1].extract_tables() or []
    except Exception:                                    # noqa: BLE001
        return None
    for table in tables:
        for row in table or []:
            if not row:
                continue
            if not re.search(label_re, str(row[0] or ""), re.I):
                continue
            for cell in row[1:]:
                v = parse_number(cell)
                if v is not None and v > 0:
                    return v
    return None


def _scaled(val: float | None, unit: str | None) -> float | None:
    if val is None:
        return None
    f = {"lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01,
         "crore": 1.0, "crores": 1.0, "million": 0.1, "millions": 0.1,
         "billion": 100.0, "billions": 100.0}.get((unit or "").lower())
    return round(val * f, 2) if f else None      # unknown unit => refuse to guess


def extract_rpt(pages: list[dict], sections: dict, pdf_path: str | None = None) -> dict:
    r = _sec_range(sections, "financial_statements")
    out = {"found": False, "total_cr": None, "source_page": None}
    if not r:
        return out
    for p in pages[r[0] - 1:r[1]]:
        if not re.search(r"related\s+party\s+(?:transactions?|disclosures?)", p["text"][:2000], re.I):
            continue
        out["found"], out["source_page"] = True, p["n"]
        m = re.search(rf"(?:total|aggregate)[^\n]{{0,80}}related\s+party[^\n]{{0,80}}?{STRICT_AMOUNT_RE}", p["text"], re.I) \
            or re.search(rf"related\s+party\s+transactions?[^\n]{{0,120}}?(?:aggregat\w+|total\w*)[^\n]{{0,60}}?{STRICT_AMOUNT_RE}", p["text"], re.I)
        if m:
            out["total_cr"] = _to_crore(m.group(1), m.group(2))
        elif pdf_path:
            out["total_cr"] = _scaled(_total_from_tables(pdf_path, p["n"], r"^\s*total"),
                                      _page_unit(p["text"]))
        break
    return out


def extract_contingent_liabilities(pages: list[dict], sections: dict,
                                   pdf_path: str | None = None) -> dict:
    r = _sec_range(sections, "financial_statements")
    out = {"found": False, "total_cr": None, "source_page": None}
    if not r:
        return out
    for p in pages[r[0] - 1:r[1]]:
        if not re.search(r"contingent\s+liabilit(?:y|ies)", p["text"], re.I):
            continue
        out["found"], out["source_page"] = True, p["n"]
        m = re.search(rf"contingent\s+liabilit(?:y|ies)[^\n]{{0,200}}?{STRICT_AMOUNT_RE}", p["text"], re.I | re.S)
        if m:
            out["total_cr"] = _to_crore(m.group(1), m.group(2))
        elif pdf_path:
            out["total_cr"] = _scaled(
                _total_from_tables(pdf_path, p["n"], r"^\s*total|contingent\s+liabilit"),
                _page_unit(p["text"]))
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
