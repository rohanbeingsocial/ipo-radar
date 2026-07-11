"""Generate a synthetic mini-RHP PDF that mirrors SEBI-ICDR structure.

Used for (a) end-to-end pipeline verification and (b) seeding the demo
analysis. The company is fictional; the document says so on every page.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

styles = getSampleStyleSheet()
H = ParagraphStyle("H", parent=styles["Heading1"], fontSize=13, spaceAfter=8)
SUB = ParagraphStyle("SUB", parent=styles["Heading2"], fontSize=10.5, spaceAfter=6)
P = ParagraphStyle("P", parent=styles["BodyText"], fontSize=8.5, leading=11.5)
SMALL = ParagraphStyle("SMALL", parent=styles["BodyText"], fontSize=7.5, leading=9.5)

GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
])


def para(text, style=P):
    return Paragraph(text, style)


def build(path: str) -> None:
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm)
    story = []

    # ---------------- Cover page ----------------
    story += [
        para("RED HERRING PROSPECTUS (SYNTHETIC SAMPLE — FICTIONAL COMPANY, FOR SOFTWARE TESTING ONLY)", SMALL),
        Spacer(1, 8),
        Paragraph("ACME PRECISION INDUSTRIES LIMITED", ParagraphStyle("T", parent=H, fontSize=16)),
        para("Our Company was incorporated as Acme Precision Industries Private Limited on June 14, 1994 "
             "at Pune, Maharashtra. Corporate Identity Number: U29199PN1994PLC081234."),
        Spacer(1, 6),
        para("PUBLIC OFFER OF UP TO 45,180,000 EQUITY SHARES OF FACE VALUE OF RS. 10 EACH COMPRISING A "
             "FRESH ISSUE OF UP TO 18,070,000 EQUITY SHARES AGGREGATING UP TO RS. 60,000 LAKHS AND AN "
             "OFFER FOR SALE OF UP TO 27,110,000 EQUITY SHARES AGGREGATING UP TO RS. 90,000 LAKHS BY THE "
             "SELLING SHAREHOLDERS."),
        Spacer(1, 6),
        para("PRICE BAND: RS. 315 TO RS. 332 PER EQUITY SHARE OF FACE VALUE OF RS. 10 EACH. "
             "BID LOT: 45 EQUITY SHARES AND IN MULTIPLES THEREOF."),
        para("The Equity Shares are proposed to be listed on BSE and NSE."),
        PageBreak(),
    ]

    # ---------------- Printed TOC ----------------
    toc_rows = [
        ("OFFER DOCUMENT SUMMARY", 3), ("RISK FACTORS", 4), ("GENERAL INFORMATION", 6),
        ("CAPITAL STRUCTURE", 7), ("OBJECTS OF THE OFFER", 8), ("BASIS FOR OFFER PRICE", 9),
        ("INDUSTRY OVERVIEW", 10), ("OUR BUSINESS", 11), ("OUR MANAGEMENT", 12),
        ("OUR PROMOTERS AND PROMOTER GROUP", 13), ("DIVIDEND POLICY", 14),
        ("RESTATED FINANCIAL STATEMENTS", 15), ("MANAGEMENT'S DISCUSSION AND ANALYSIS", 20),
        ("OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS", 21),
        ("GOVERNMENT AND OTHER APPROVALS", 22), ("OFFER STRUCTURE", 23),
        ("TERMS OF THE OFFER", 24),
    ]
    story += [para("TABLE OF CONTENTS", H)]
    for title, pg in toc_rows:
        story.append(para(f"{title} {'.' * 40} {pg}", SMALL))
    story.append(PageBreak())

    # ---------------- Offer Document Summary ----------------
    story += [
        para("OFFER DOCUMENT SUMMARY", H),
        para("This summary is provided pursuant to the SEBI ICDR Regulations. Our Company is a precision "
             "engineering components manufacturer serving the automotive and industrial sectors. Our revenue "
             "from operations was Rs. 4,52,180 lakhs in Fiscal 2024, Rs. 3,61,420 lakhs in Fiscal 2023 and "
             "Rs. 2,98,540 lakhs in Fiscal 2022. Restated profit for the year was Rs. 49,870 lakhs, "
             "Rs. 37,720 lakhs and Rs. 29,430 lakhs respectively."),
        PageBreak(),
    ]

    # ---------------- Risk Factors ----------------
    risk_paras = [
        "Our top 10 customers contributed 62.4% of our revenue from operations in Fiscal 2024 and any loss "
        "of one or more such customers, or a material reduction in their purchases, could materially and "
        "adversely affect our business, results of operations and financial condition. We do not have "
        "long-term agreements with several of these customers and typically operate on a purchase-order basis.",
        "We depend on a limited number of suppliers for certain key raw materials, including specialty alloy "
        "steel. Our top 5 suppliers accounted for 44.1% of our total raw material purchases in Fiscal 2024. "
        "Any disruption in supply could adversely impact our production schedules.",
        "Exports contributed 34.2% of our revenue from operations in Fiscal 2024, exposing us to foreign "
        "currency exchange rate fluctuations, principally in USD and EUR, which could adversely affect margins.",
        "The industry in which we operate is cyclical in nature and is affected by macroeconomic conditions "
        "affecting the automotive sector, capital investment cycles and commodity price movements.",
        "Our business requires various licences and approvals, and the failure to obtain, maintain or renew "
        "such licences and approvals in a timely manner could adversely affect our operations.",
        "We depend significantly on our Promoters and Key Managerial Personnel, and the loss of any of their "
        "services could adversely impact our strategic direction and operations.",
        "We operate in a highly competitive industry with both organized and unorganized players, and "
        "increased competition may lead to pricing pressure and reduction in our market share.",
        "There are outstanding legal proceedings involving our Company, our Promoters and our Directors, "
        "details of which are set out in the chapter titled Outstanding Litigation and Material Developments.",
        "Our insurance coverage may not be adequate to protect us against all material hazards, which may "
        "adversely affect our business and results of operations.",
        "Any downgrade in our credit ratings could increase our borrowing costs and constrain our access to "
        "capital, which would adversely affect our financial condition.",
    ]
    story += [para("RISK FACTORS", H),
              para("An investment in Equity Shares involves a high degree of risk. Investors should carefully "
                   "consider the following risks, in addition to the other information in this Red Herring "
                   "Prospectus, before making an investment decision. The risks are presented in order of materiality.")]
    for i, rp in enumerate(risk_paras, 1):
        story.append(para(f"{i}. {rp}"))
    story.append(PageBreak())

    # ---------------- General Information ----------------
    story += [
        para("GENERAL INFORMATION", H),
        para("Registered Office: Plot 14, MIDC Industrial Area, Bhosari, Pune 411 026, Maharashtra, India. "
             "Book Running Lead Managers: Meridian Capital Advisors Limited. Registrar to the Offer: "
             "TrueLink Intime Registry Limited. Statutory Auditors: R. K. Shah & Associates, Chartered "
             "Accountants. The Offer is being made through the Book Building Process."),
        PageBreak(),
    ]

    # ---------------- Capital Structure ----------------
    story += [
        para("CAPITAL STRUCTURE", H),
        para("The pre-Offer shareholding of our Promoters is 68.40% of the issued, subscribed and paid-up "
             "equity share capital of our Company. Following the completion of the Offer, the post-Offer "
             "shareholding of our Promoters will be 54.20% of the post-Offer equity share capital."),
        para("None of the Equity Shares held by our Promoters are pledged or otherwise encumbered."),
        para("The authorised share capital of our Company is Rs. 20,000 lakhs divided into 200,000,000 "
             "Equity Shares of face value of Rs. 10 each."),
        PageBreak(),
    ]

    # ---------------- Objects of the Offer ----------------
    story += [
        para("OBJECTS OF THE OFFER", H),
        para("Our Company proposes to utilise the Net Proceeds from the Fresh Issue towards the following objects:"),
        para("1. Repayment or prepayment, in full or in part, of certain outstanding borrowings availed by "
             "our Company aggregating up to Rs. 22,000 lakhs."),
        para("2. Funding capital expenditure towards setting up of a new manufacturing facility at Chakan, "
             "Pune aggregating up to Rs. 25,000 lakhs."),
        para("3. Funding incremental working capital requirements of our Company aggregating up to Rs. 8,000 lakhs."),
        para("4. General corporate purposes."),
        para("The Offer for Sale proceeds will be received by the Selling Shareholders and our Company will "
             "not receive any proceeds from the Offer for Sale."),
        PageBreak(),
    ]

    # ---------------- Basis for Offer Price ----------------
    peer_table = Table([
        ["Name of Company", "EPS (Rs.)", "P/E", "RoNW (%)"],
        ["Acme Precision Industries Limited*", "12.90", "-", "20.30"],
        ["Zenith Industrial Ltd", "22.40", "31.20", "18.40"],
        ["Precision Engineered Systems Ltd", "15.80", "42.50", "16.20"],
        ["NovaTech Fabrication Ltd", "9.60", "27.90", "14.80"],
    ], colWidths=[200, 70, 70, 70])
    peer_table.setStyle(GRID)
    story += [
        para("BASIS FOR OFFER PRICE", H),
        para("The Offer Price will be determined by our Company in consultation with the BRLM on the basis "
             "of the Book Building Process. The P/E ratio at the upper end of the Price Band is 38.6 times, "
             "based on restated diluted EPS of Rs. 8.60 for Fiscal 2024 on the post-Offer share capital."),
        para("Comparison with listed industry peers (financial year ended March 31, 2024):"),
        peer_table,
        para("* Financial information of our Company is based on the Restated Financial Statements.", SMALL),
        PageBreak(),
    ]

    # ---------------- Industry Overview ----------------
    story += [
        para("INDUSTRY OVERVIEW", H),
        para("The Indian precision engineering components industry was valued at approximately Rs. 1,85,000 "
             "crore in Fiscal 2024 and is expected to grow at a CAGR of 11-13% through Fiscal 2029, driven "
             "by automotive premiumisation, localisation of supply chains and growth in industrial capex. "
             "(Source: Industry report commissioned by our Company.)"),
        PageBreak(),
    ]

    # ---------------- Our Business ----------------
    story += [
        para("OUR BUSINESS", H),
        para("We are one of the leading manufacturers of high-tolerance precision machined components in "
             "India by installed capacity. We operate four manufacturing facilities with an aggregate "
             "installed capacity of 48,500 MT per annum, and supply to 14 of the top 20 automotive OEMs "
             "operating in India."),
        para("Our top 10 customers contributed 62.4% of our revenue from operations in Fiscal 2024. Exports "
             "contributed 34.2% of our revenue from operations in Fiscal 2024, spanning 17 countries."),
        para("Our order book as of March 31, 2024 stood at Rs. 2,10,400 lakhs, providing revenue visibility "
             "of approximately 18 months."),
        PageBreak(),
    ]

    # ---------------- Management ----------------
    story += [
        para("OUR MANAGEMENT", H),
        para("Our Board comprises 8 Directors, including 4 Independent Directors (of whom one is a woman "
             "director). The Audit Committee, Nomination and Remuneration Committee and Stakeholders "
             "Relationship Committee have been constituted in compliance with the Companies Act and the "
             "SEBI Listing Regulations."),
        PageBreak(),
    ]

    # ---------------- Promoters ----------------
    story += [
        para("OUR PROMOTERS AND PROMOTER GROUP", H),
        para("Rajesh Mehta, aged 58 years, is one of our Promoters and serves as our Chairman and Managing "
             "Director. He has an experience of over 30 years in the precision engineering industry."),
        para("Anita Mehta, aged 54 years, is one of our Promoters and serves as a Whole-time Director. She "
             "has an experience of over 22 years in corporate finance and operations."),
        PageBreak(),
    ]

    # ---------------- Dividend Policy ----------------
    story += [
        para("DIVIDEND POLICY", H),
        para("Our Company has not declared or paid any dividend on the Equity Shares during the last three "
             "Fiscals and the current Fiscal. Any future determination as to the declaration of dividends "
             "will be at the discretion of our Board."),
        PageBreak(),
    ]

    # ---------------- Financial statements ----------------
    story += [
        para("RESTATED FINANCIAL STATEMENTS", H),
        para("Independent Auditor's Examination Report on the Restated Consolidated Financial Information: "
             "In our opinion, the Restated Consolidated Financial Information, prepared in accordance with "
             "the SEBI ICDR Regulations, gives a true and fair view in conformity with the accounting "
             "principles generally accepted in India. Our report does not contain any qualification."),
        PageBreak(),
    ]

    bs = Table([
        ["Particulars", "As at March 31, 2024", "As at March 31, 2023", "As at March 31, 2022"],
        ["Total non-current assets", "3,56,900.00", "3,03,400.00", "2,65,600.00"],
        ["Inventories", "72,150.00", "60,890.00", "51,230.00"],
        ["Trade receivables", "88,340.00", "66,120.00", "54,410.00"],
        ["Cash and cash equivalents", "21,050.00", "15,220.00", "11,980.00"],
        ["Total current assets", "2,41,300.00", "1,98,700.00", "1,66,200.00"],
        ["Total assets", "5,98,200.00", "5,02,100.00", "4,31,800.00"],
        ["Total equity", "2,45,600.00", "1,96,400.00", "1,58,900.00"],
        ["Long-term borrowings", "55,400.00", "62,300.00", "71,900.00"],
        ["Short-term borrowings", "41,200.00", "49,800.00", "45,600.00"],
        ["Total current liabilities", "1,68,900.00", "1,49,300.00", "1,32,700.00"],
    ], colWidths=[150, 105, 105, 105])
    bs.setStyle(GRID)
    story += [
        para("RESTATED CONSOLIDATED STATEMENT OF ASSETS AND LIABILITIES", SUB),
        para("(Rs. in lakhs)", SMALL), bs, PageBreak(),
    ]

    pl = Table([
        ["Particulars", "Fiscal 2024", "Fiscal 2023", "Fiscal 2022"],
        ["Revenue from operations", "4,52,180.00", "3,61,420.00", "2,98,540.00"],
        ["Other income", "3,150.00", "2,410.00", "1,890.00"],
        ["Total income", "4,55,330.00", "3,63,830.00", "3,00,430.00"],
        ["Cost of materials consumed", "2,71,300.00", "2,19,600.00", "1,84,200.00"],
        ["Employee benefits expense", "61,220.00", "50,140.00", "42,380.00"],
        ["Finance costs", "8,940.00", "9,480.00", "10,110.00"],
        ["Depreciation and amortisation expense", "15,310.00", "13,890.00", "12,760.00"],
        ["Total expenses", "3,88,440.00", "3,13,220.00", "2,60,950.00"],
        ["Profit before tax", "66,890.00", "50,610.00", "39,480.00"],
        ["Total tax expense", "17,020.00", "12,890.00", "10,050.00"],
        ["Profit for the year", "49,870.00", "37,720.00", "29,430.00"],
    ], colWidths=[170, 95, 95, 95])
    pl.setStyle(GRID)
    story += [
        para("RESTATED CONSOLIDATED STATEMENT OF PROFIT AND LOSS", SUB),
        para("(Rs. in lakhs)", SMALL), pl, PageBreak(),
    ]

    cf = Table([
        ["Particulars", "Fiscal 2024", "Fiscal 2023", "Fiscal 2022"],
        ["Net cash generated from operating activities", "52,310.00", "41,080.00", "30,240.00"],
        ["Purchase of property, plant and equipment", "(28,540.00)", "(24,110.00)", "(19,870.00)"],
        ["Net cash used in investing activities", "(30,120.00)", "(25,480.00)", "(21,300.00)"],
        ["Net cash used in financing activities", "(16,360.00)", "(12,360.00)", "(7,540.00)"],
    ], colWidths=[190, 90, 90, 90])
    cf.setStyle(GRID)
    story += [
        para("RESTATED CONSOLIDATED STATEMENT OF CASH FLOWS", SUB),
        para("(Rs. in lakhs)", SMALL), cf, PageBreak(),
    ]

    story += [
        para("NOTES TO THE RESTATED FINANCIAL STATEMENTS", SUB),
        para("Related Party Transactions (Ind AS 24): Total related party transactions aggregated to "
             "Rs. 12,450 lakhs for Fiscal 2024, primarily comprising remuneration to Key Managerial "
             "Personnel and lease rentals paid to entities forming part of the Promoter Group."),
        para("Contingent Liabilities (Ind AS 37): As at March 31, 2024, contingent liabilities not provided "
             "for amounted to Rs. 18,600 lakhs, comprising disputed indirect tax demands and bank guarantees."),
        PageBreak(),
    ]

    # ---------------- MD&A ----------------
    story += [
        para("MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS", H),
        para("Fiscal 2024 compared to Fiscal 2023: Our revenue from operations increased by 25.1% to "
             "Rs. 4,52,180 lakhs, driven by strong growth in export volumes and the ramp-up of our fourth "
             "facility. EBITDA margin improved on account of operating leverage and softening raw material "
             "prices. Profit for the year increased by 32.2% to Rs. 49,870 lakhs."),
        PageBreak(),
    ]

    # ---------------- Litigation ----------------
    lit = Table([
        ["Category", "Number of matters", "Amount involved (Rs. in lakhs)"],
        ["Criminal proceedings", "2", "410.00"],
        ["Tax proceedings", "14", "2,860.00"],
        ["Material civil litigations", "6", "850.00"],
    ], colWidths=[190, 110, 160])
    lit.setStyle(GRID)
    story += [
        para("OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS", H),
        para("A summary of outstanding litigation involving our Company, Promoters and Directors is set out below:"),
        lit,
        para("The aggregate amount involved in the above proceedings is Rs. 4,120 lakhs. The criminal "
             "proceedings pertain to alleged violations of labour legislation involving one of our Promoters "
             "and are being contested."),
        PageBreak(),
    ]

    # ---------------- Approvals ----------------
    story += [
        para("GOVERNMENT AND OTHER APPROVALS", H),
        para("Our Company has obtained all material consents, licences and approvals required to undertake "
             "the Offer and to carry on its business, except certain approvals which are pending renewal in "
             "the ordinary course."),
        PageBreak(),
    ]

    # ---------------- Offer Structure ----------------
    story += [
        para("OFFER STRUCTURE", H),
        para("The Offer comprises a Fresh Issue of up to 18,070,000 Equity Shares aggregating up to "
             "Rs. 60,000 lakhs and an Offer for Sale of up to 27,110,000 Equity Shares aggregating up to "
             "Rs. 90,000 lakhs. Not less than 50% of the Offer shall be allocated to Qualified Institutional "
             "Buyers, not less than 15% to Non-Institutional Bidders and not less than 35% to Retail "
             "Individual Bidders. Bid Lot: 45 Equity Shares. The Equity Shares are proposed to be listed on "
             "BSE and NSE."),
        PageBreak(),
    ]

    # ---------------- Terms ----------------
    story += [
        para("TERMS OF THE OFFER", H),
        para("The Equity Shares being offered shall be subject to the provisions of the Companies Act, the "
             "SEBI ICDR Regulations, our Memorandum and Articles of Association and the terms of this Red "
             "Herring Prospectus. The Equity Shares shall rank pari passu in all respects with the existing "
             "Equity Shares. This synthetic document is fictional and generated solely for software testing."),
    ]

    doc.build(story)
    print(f"Wrote {path}")


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "sample_data" / "synthetic_rhp.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(str(out))
