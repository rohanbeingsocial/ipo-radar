"""Stage 3: restated financial statements -> normalized 3-year metric series.

Strategy: locate anchor pages (Restated P&L / Assets & Liabilities / Cash Flow,
plus the ICDR-mandated summary financials in the Offer Document Summary), run
pdfplumber table extraction on those pages, label rows via a synonym map, map
columns to fiscal years from header cells, and normalize everything to ₹ crore.
"""
from __future__ import annotations

import re

import pdfplumber

# metric -> row-label regex (case-insensitive). Order matters: first match wins,
# and more specific labels come before generic ones.
ROW_SYNONYMS: list[tuple[str, str]] = [
    ("revenue", r"revenue\s+from\s+operations?"),
    ("other_income", r"^other\s+income"),
    ("total_income", r"^total\s+income"),
    ("cost_of_materials", r"cost\s+of\s+(?:materials|goods)"),
    ("employee_costs", r"employee\s+benefits?\s+expenses?"),
    ("finance_costs", r"^finance\s+costs?"),
    ("depreciation", r"depreciation\s+and\s+amorti[sz]ation"),
    ("total_expenses", r"^total\s+expenses"),
    ("pbt", r"(?:restated\s+)?profit\s*/?\(?(?:loss)?\)?\s+before\s+tax"),
    ("tax_expense", r"^(?:total\s+)?tax\s+expenses?"),
    ("pat", r"(?:restated\s+)?(?:net\s+)?profit\s*/?\(?(?:loss)?\)?\s+(?:after\s+tax|for\s+the\s+(?:year|period))"),
    ("ebitda", r"\bEBITDA\b"),
    ("total_assets", r"^total\s+assets"),
    ("net_worth", r"(?:^total\s+equity$|^net\s*worth|equity\s+attributable\s+to\s+(?:the\s+)?owners)"),
    ("borrowings_lt", r"(?:long[\s-]?term|non[\s-]?current)\s+borrowings"),
    ("borrowings_st", r"(?:short[\s-]?term|current)\s+borrowings"),
    ("total_debt", r"^total\s+(?:borrowings|debt)"),
    ("receivables", r"trade\s+receivables"),
    ("inventory", r"^inventor(?:y|ies)"),
    ("cash", r"cash\s+and\s+cash\s+equivalents"),
    ("current_assets", r"^total\s+current\s+assets"),
    ("current_liabilities", r"^total\s+current\s+liabilities"),
    # "Net cash flow generated from/(used in) operating activities" and other
    # combined-sign phrasings: allow anything short between "net cash" and the activity
    ("cfo", r"net\s+cash\s+(?:flows?\s+)?[^,;.]{0,40}?operat(?:ing|ions)"),
    ("cfi", r"net\s+cash\s+(?:flows?\s+)?[^,;.]{0,40}?investing\s+activit"),
    ("cff", r"net\s+cash\s+(?:flows?\s+)?[^,;.]{0,40}?financing\s+activit"),
    ("capex", r"(?:purchase|acquisition)\s+of\s+property,?\s+plant"),
]

STATEMENT_ANCHORS = [
    r"restated\s+(?:consolidated\s+|standalone\s+)?statement\s+of\s+profit\s+and\s+loss",
    r"restated\s+(?:consolidated\s+|standalone\s+)?statement\s+of\s+assets\s+and\s+liabilities",
    r"restated\s+(?:consolidated\s+|standalone\s+)?(?:statement\s+of\s+)?cash\s+flows?",
    r"summary\s+(?:of\s+)?(?:restated\s+)?financial\s+information",
    r"balance\s+sheet",
    r"statement\s+of\s+profit\s+and\s+loss",
    r"cash\s+flow\s+statement",
]

UNIT_FACTORS = {"lakhs": 0.01, "lacs": 0.01, "lakh": 0.01,
                "crores": 1.0, "crore": 1.0, "cr": 1.0,
                "millions": 0.1, "million": 0.1, "mn": 0.1,
                "billions": 100.0, "billion": 100.0}

_FY_PAT = r"(?:march\s+31(?:st)?,?\s*|31(?:st)?\s+(?:of\s+)?march,?\s*|fiscal\s+|financial\s+year\s+|fy\s*)((?:20)?\d{2})"
FY_HEADER_RE = re.compile(_FY_PAT, re.I)

# Stub-period column headers (quarter-end dates): their values must be
# consumed but never assigned to a fiscal year.
_STUB_PAT = (r"(?:december\s+31|september\s+30|june\s+30|"
             r"31(?:st)?\s+(?:of\s+)?december|30(?:th)?\s+(?:of\s+)?(?:september|june))"
             r"(?:st|nd|rd|th)?\s*,?\s*((?:20)?\d{2})")
_COL_HEADER_RE = re.compile(rf"(?P<stub>{_STUB_PAT})|(?P<fy>{_FY_PAT})", re.I)

# Trailing Ind-AS row references like "(I)", "(III = I+II)", "(VIII= V+VI-VII)".
# Only roman-numeral arithmetic is stripped so "(India)" etc. survive.
ROMAN_TAIL_RE = re.compile(r"\s*\(\s*[IVX][IVX\s=+\-]*\)\s*$")

_DASH_CELLS = {"-", "–", "—", "nil", "na", "n.a."}

# Cash-flow movement rows ("Proceeds from long-term borrowings", "(Increase)/
# Decrease in trade receivables") would otherwise match balance-sheet metrics.
_FLOW_LABEL_RE = re.compile(r"proceeds|repayments?\b|\bincrease\b|\bdecrease\b", re.I)

# "(a) Inventories", "(i) Borrowings", "1. Revenue" — Ind-AS enumeration prefixes
_ENUM_PREFIX_RE = re.compile(r"^\(?(?:[a-z]|[ivx]{1,4}|\d{1,2})[.)]\s*", re.I)


def parse_number(raw: str) -> float | None:
    """Handles Indian grouping (1,23,456.78), parenthesised negatives, dashes."""
    if raw is None:
        return None
    s = str(raw).strip().replace("₹", "").replace("Rs.", "").replace("*", "").strip()
    if s in {"", "-", "–", "—", "NA", "N.A.", "Nil", "nil"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").strip()
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    val = float(s)
    return -val if neg else val


def detect_unit(text: str) -> tuple[str, float] | None:
    m = re.search(r"(?:₹|rs\.?|rupees|inr)\s*(?:in\s+)?(lakhs?|lacs|crores?|cr|millions?|mn|billions?)", text, re.I)
    if not m:
        m = re.search(r"\(\s*(?:all\s+amounts?\s+)?(?:₹|rs\.?|inr)?\s*in\s+(lakhs?|lacs|crores?|millions?|mn)", text, re.I)
    if m:
        unit = m.group(1).lower()
        return unit, UNIT_FACTORS.get(unit, 1.0)
    return None


def _fiscal_labels_from_header(cells: list[str]) -> list[str | None]:
    labels: list[str | None] = []
    for cell in cells:
        if not cell:
            labels.append(None)
            continue
        m = FY_HEADER_RE.search(str(cell))
        if m:
            yy = int(m.group(1)) % 100
            labels.append(f"FY{yy:02d}")
        else:
            labels.append(None)
    return labels


def _clean_label(label: str) -> str:
    label_norm = re.sub(r"\s+", " ", str(label or "")).strip()
    label_norm = _ENUM_PREFIX_RE.sub("", label_norm, count=1)
    return ROMAN_TAIL_RE.sub("", label_norm)


def _match_metric(label: str) -> str | None:
    label_norm = _clean_label(label)
    if _FLOW_LABEL_RE.search(label_norm):
        return None
    for metric, pattern in ROW_SYNONYMS:
        if re.search(pattern, label_norm, re.I):
            return metric
    return None


def _fy_labels_from_text(page_text: str) -> list[str]:
    """Column headers for statements rendered without table ruling: the fiscal
    years appear near the top of the page ("...ended March 31, 2026" etc.),
    latest first, one per column. A stub-period column (e.g. "December 31,
    2025") becomes a STUBnn label so its values are consumed but not kept."""
    labels: list[str] = []
    for m in _COL_HEADER_RE.finditer(page_text[:3000]):
        yy = int(m.group(0)[-2:])
        lab = f"STUB{yy:02d}" if m.group("stub") else f"FY{yy:02d}"
        if lab not in labels:
            labels.append(lab)
        if len(labels) == 5:
            break
    return labels if sum(1 for l in labels if l.startswith("FY")) >= 2 else []


# Value tokens on a label line must carry grouping/decimals so bare integers
# from dates ("March 31, 2026") or note references are never read as figures.
_NUM_TOKEN = r"\(?-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\)?|\(?-?\d+\.\d+\)?"
_CELL_TOKEN = rf"(?:{_NUM_TOKEN}|[-–—])"
# the run of cell values at the end of a label line ("Total borrowings - 150.00")
_INLINE_TAIL_RE = re.compile(rf"(?:^|\s)((?:{_CELL_TOKEN}\s+)*{_CELL_TOKEN})\s*$")
_DASH_TOKENS = {"-", "–", "—"}


_NONCUR_LIAB_RE = re.compile(r"non[\s-]?current\s+liabilit", re.I)
_CUR_LIAB_RE = re.compile(r"^current\s+liabilit", re.I)
_BARE_BORROWINGS_RE = re.compile(r"^borrowings?$", re.I)


def _rows_from_text(page_text: str, fy_labels: list[str]) -> list[tuple[str, list[float | None]]]:
    """Parse borderless statements where pdfplumber finds no table: a row is a
    label line followed by one value line per fiscal column (or the values sit
    at the end of the label line itself). A leading Notes-column reference
    (bare 1-3 digit integer) is dropped; rows with any other column-count
    mismatch — e.g. proforma + fiscal-year columns sharing a header — are
    dropped rather than guessed."""
    ncols = len(fy_labels)
    lines = page_text.splitlines()
    rows: list[tuple[str, list[float | None]]] = []
    liab_ctx: str | None = None  # Ind-AS sheets label both debt rows just "Borrowings"
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        if raw:
            if _NONCUR_LIAB_RE.search(raw):
                liab_ctx = "lt"
            elif _CUR_LIAB_RE.search(raw):
                liab_ctx = "st"
        metric = _match_metric(raw) if raw else None
        if not metric and raw and liab_ctx and _BARE_BORROWINGS_RE.match(_clean_label(raw)):
            metric = f"borrowings_{liab_ctx}"
        if not metric:
            i += 1
            continue
        m_tail = _INLINE_TAIL_RE.search(raw)
        if m_tail:
            toks = m_tail.group(1).split()
            cells = [None if t in _DASH_TOKENS else parse_number(t) for t in toks]
            if len(cells) in (ncols, ncols + 1):
                # ncols+1: leading token is a Notes-column reference
                rows.append((metric, cells[-ncols:]))
                i += 1
                continue
        vals: list[float | None] = []
        first_tok: str | None = None
        junk_skips = 2  # stray artifacts ("Total A") between label and figures
        j = i + 1
        while j < len(lines) and len(vals) < ncols + 2:
            s = lines[j].strip()
            if not s:
                j += 1
                continue
            if s.strip("().").lower() in _DASH_CELLS:
                vals.append(None)  # placeholder cell consumes a column
            else:
                v = parse_number(s)
                if v is None:
                    if (not vals and junk_skips and len(s) < 12
                            and not any(ch.isdigit() for ch in s) and not _match_metric(s)):
                        junk_skips -= 1
                        j += 1
                        continue
                    break  # next label reached
                if not vals:  # token of the first cell only — a note ref precedes all values
                    first_tok = s
                vals.append(v)
            j += 1
        if len(vals) == ncols + 1 and first_tok and re.fullmatch(r"\d{1,3}", first_tok):
            vals = vals[1:]  # leading Notes-column reference
        if len(vals) == ncols and any(v is not None for v in vals):
            rows.append((metric, vals))
            i = j
        else:
            i += 1
    return rows


def find_statement_pages(pages: list[dict], sections: dict) -> list[int]:
    """Anchor pages inside the financials chapter + the offer summary."""
    candidates: list[int] = []
    ranges = []
    for key in ("financial_statements", "other_financial_info", "offer_summary"):
        sec = sections.get(key) or {}
        if sec.get("found"):
            ranges.append((sec["page_start"], sec["page_end"]))
    if not ranges:  # degrade: scan whole doc
        ranges = [(1, len(pages))]
    for start, end in ranges:
        for p in pages[start - 1:end]:
            head = p["text"][:2500].lower()
            if any(re.search(a, head) for a in STATEMENT_ANCHORS):
                candidates.append(p["n"])
    return sorted(set(candidates))


def extract_financials(pdf_path: str, pages: list[dict], sections: dict) -> dict:
    """Returns {"series": {fiscal_label: {metric: value_cr}}, "fiscal_order": [...],
    "unit": str, "source_pages": {metric: page}, "confidence": {metric: float}}"""
    anchor_pages = find_statement_pages(pages, sections)
    series: dict[str, dict[str, float]] = {}
    source_pages: dict[str, int] = {}
    confidence: dict[str, float] = {}
    unit_name, factor = "crores", 1.0  # default when no unit marker found

    with pdfplumber.open(pdf_path) as pdf:
        # each anchor page + following 3 pages (statements span pages)
        scan = sorted({n for a in anchor_pages for n in range(a, min(a + 4, len(pages) + 1))})

        # A statement block often states its unit on only one of its pages
        # ("All amounts in ₹ Millions" on the cash-flow page but not the P&L),
        # so unmarked pages take the nearest marker within their contiguous run.
        page_units = {n: d for n in scan if (d := detect_unit(pages[n - 1]["text"][:2500]))}
        resolved: dict[int, tuple[str, float]] = {}
        run: list[int] = []
        for n in scan + [None]:
            if run and (n is None or n != run[-1] + 1):
                marked = [m for m in run if m in page_units]
                for p_ in run:
                    if marked:
                        nearest = min(marked, key=lambda m: (abs(m - p_), m > p_))
                        resolved[p_] = page_units[nearest]
                run = []
            if n is not None:
                run.append(n)

        carried_fy: list[str] = []  # statement column headers carried onto continuation pages
        prev_n: int | None = None
        for n in scan:
            page = pdf.pages[n - 1]
            page_text = pages[n - 1]["text"]
            if n in resolved:
                unit_name, factor = resolved[n]
            if prev_n is not None and n != prev_n + 1:
                carried_fy = []
            prev_n = n
            page_fy = _fy_labels_from_text(page_text)
            if page_fy:
                carried_fy = page_fy
            try:
                tables = page.extract_tables()
            except Exception:
                tables = []
            for table in tables or []:
                if not table or len(table) < 2:
                    continue
                # locate the header row with fiscal labels
                fiscal_cols: list[tuple[int, str]] = []
                header_idx = 0
                for ri, row in enumerate(table[:4]):
                    labels = _fiscal_labels_from_header([c or "" for c in row])
                    cols = [(ci, lab) for ci, lab in enumerate(labels) if lab]
                    if len(cols) >= 2:
                        fiscal_cols, header_idx = cols, ri
                        break
                if not fiscal_cols:
                    continue
                for row in table[header_idx + 1:]:
                    if not row or not row[0]:
                        continue
                    metric = _match_metric(row[0])
                    if not metric:
                        continue
                    for ci, fy in fiscal_cols:
                        if ci >= len(row):
                            continue
                        val = parse_number(row[ci])
                        if val is None:
                            continue
                        fy_series = series.setdefault(fy, {})
                        if metric not in fy_series:  # first (statement) value wins
                            fy_series[metric] = round(val * factor, 2)
                            source_pages.setdefault(metric, n)
                            confidence.setdefault(metric, 0.9)

            # Borderless statements (no ruling lines) defeat extract_tables;
            # fall back to parsing the page text. Table values win via the
            # metric-not-in-series guard above.
            if carried_fy:
                for metric, vals in _rows_from_text(page_text, carried_fy):
                    for fy, val in zip(carried_fy, vals):
                        if val is None or fy.startswith("STUB"):
                            continue
                        fy_series = series.setdefault(fy, {})
                        if metric not in fy_series:
                            fy_series[metric] = round(val * factor, 2)
                            source_pages.setdefault(metric, n)
                            confidence.setdefault(metric, 0.7)

    _derive_metrics(series)
    fiscal_order = sorted(series.keys(), key=lambda l: int(l[2:]), reverse=True)
    return {"series": series, "fiscal_order": fiscal_order, "unit": unit_name,
            "source_pages": source_pages, "confidence": confidence,
            "anchor_pages": anchor_pages}


def _derive_metrics(series: dict[str, dict[str, float]]) -> None:
    for fy, m in series.items():
        if "total_debt" not in m and ("borrowings_lt" in m or "borrowings_st" in m):
            m["total_debt"] = round(m.get("borrowings_lt", 0) + m.get("borrowings_st", 0), 2)
        if "ebitda" not in m and all(k in m for k in ("pbt", "finance_costs", "depreciation")):
            m["ebitda"] = round(m["pbt"] + m["finance_costs"] + m["depreciation"], 2)
        if "revenue" not in m and "total_income" in m:
            m["revenue"] = round(m["total_income"] - m.get("other_income", 0), 2)


def get_metric(fin: dict, metric: str, rank: int = 0) -> float | None:
    order = fin.get("fiscal_order") or []
    if rank >= len(order):
        return None
    return fin["series"].get(order[rank], {}).get(metric)


def cagr(fin: dict, metric: str) -> float | None:
    order = fin.get("fiscal_order") or []
    if len(order) < 2:
        return None
    latest, oldest = get_metric(fin, metric, 0), get_metric(fin, metric, len(order) - 1)
    years = len(order) - 1
    if latest is None or oldest is None or oldest <= 0 or latest <= 0:
        return None
    return (latest / oldest) ** (1 / years) - 1
