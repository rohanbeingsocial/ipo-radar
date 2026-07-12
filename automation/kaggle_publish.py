"""Publish the refreshed IPO dataset to Kaggle as a new dataset version.

Runs weekly in CI (.github/workflows/kaggle-publish.yml). Needs a Kaggle
access token (kaggle.com/settings -> API -> Create New Token) in the
KAGGLE_API_TOKEN environment variable (GitHub repo secret). The dataset
lives at kaggle.com/datasets/<token owner>/india-mainboard-ipos-20yr and
is created automatically on the first run.

Uses kagglehub rather than the kaggle CLI: the CLI's upload endpoints
reject the newer KGAT_ access tokens (401), kagglehub accepts them and
derives the username from the token so it never needs hardcoding.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = "india-mainboard-ipos-20yr"

FILES = [
    ROOT / "ipodata" / "finalipodata_expanded_20yr.xlsx",
    ROOT / "data" / "cg_issue.csv",
    ROOT / "data" / "cg_subs.csv",
    ROOT / "data" / "cg_listing.csv",
    ROOT / "data" / "ipo_outcomes.csv",
    ROOT / "data" / "cg_details.csv",
    ROOT / "data" / "cg_gmp.csv",
]

DATASET_README = """\
# India Mainboard IPOs: 20 Years of Data

NSE/BSE **mainboard** IPOs from 2004 onward — issue structure, category-wise
subscription, listing-day prices, and post-listing price paths out to 24 months.
Refreshed weekly from a self-updating pipeline
(https://github.com/rohanbeingsocial/ipo-radar) that pulls Chittorgarh's IPO
archives, SEBI's Red-Herring Prospectus filings, and Yahoo Finance daily and
commits the results.

## Files

| File | What it is |
|---|---|
| `finalipodata_expanded_20yr.xlsx` | The joined dataset, one row per IPO. Sheets: **Expanded** (all columns), **Original** (source template), **ReadMe** (column dictionary). |
| `cg_issue.csv` | Issue structure: dates, price band, issue size, fresh/OFS split, ISIN, NSE/BSE codes. |
| `cg_subs.csv` | Final subscription multiples by category: QIB, bNII, sNII, Retail, Total. |
| `cg_listing.csv` | Listing-day open/high/low/close and listing gain vs offer price. |
| `ipo_outcomes.csv` | Post-listing outcomes from daily price paths: returns vs offer at 6m/12m/24m, drawdown bottom (depth and session), peak, sessions to each. Returns are anchored to the unadjusted day-1 close to neutralise split adjustments. |
| `cg_details.csv` | Per-IPO sector (Yahoo profile) and the reserved offer split: % QIB / % Retail / % NII, with anchor = 60% of the QIB portion. |
| `cg_gmp.csv` | Grey-market premium captured while an IPO was open, and the implied estimated listing price (offer + GMP). GMP is third-party and pre-listing only. |

Key columns are documented row-by-row in the workbook's **ReadMe** sheet.

## Sources & method

- **Chittorgarh.com** IPO archives (issue structure, subscription, listing-day prices).
- **SEBI** Red-Herring Prospectus filings (document-level features scored by the
  open-source pipeline in the repo above).
- **Yahoo Finance** daily closes for post-listing paths; long-horizon coverage
  skews to survivors (delisted names drop out), so treat 24m columns accordingly.

## License / disclaimer

Compiled facts from the public sources credited above; verify against primary
sources before relying on any figure. **This dataset is for research and
education. It is not investment advice and not a recommendation.**
"""


def main() -> int:
    has_token = os.environ.get("KAGGLE_API_TOKEN", "").strip()
    has_pair = os.environ.get("KAGGLE_USERNAME", "").strip() and os.environ.get(
        "KAGGLE_KEY", ""
    ).strip()
    if not (has_token or has_pair):
        print("KAGGLE_API_TOKEN not set - skipping publish")
        return 0

    missing = [str(p) for p in FILES if not p.exists()]
    if missing:
        print("missing dataset files:", ", ".join(missing))
        return 1

    with open(ROOT / "data" / "cg_issue.csv", encoding="utf-8", newline="") as f:
        n_ipos = sum(1 for _ in f) - 1

    import kagglehub  # deferred so the no-credentials skip path needs no install

    user = kagglehub.whoami()["username"]

    stage = Path(tempfile.mkdtemp(prefix="kaggle_stage_"))
    for p in FILES:
        shutil.copy2(p, stage / p.name)
    (stage / "README.md").write_text(DATASET_README, encoding="utf-8")

    msg = f"auto refresh {datetime.now(timezone.utc):%Y-%m-%d} ({n_ipos} IPOs)"
    kagglehub.dataset_upload(f"{user}/{SLUG}", str(stage), version_notes=msg)
    print(f"published kaggle.com/datasets/{user}/{SLUG}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
