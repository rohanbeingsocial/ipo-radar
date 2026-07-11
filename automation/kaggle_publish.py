"""Publish the refreshed IPO dataset to Kaggle as a new dataset version.

Runs monthly in CI (.github/workflows/kaggle-publish.yml). Needs
KAGGLE_USERNAME and KAGGLE_KEY in the environment (GitHub repo secrets,
values from kaggle.com/settings -> API -> Create New Token). The dataset
lives at kaggle.com/datasets/<KAGGLE_USERNAME>/india-mainboard-ipos-20yr
and is created automatically (public) on the first successful run.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = "india-mainboard-ipos-20yr"
TITLE = "India Mainboard IPOs: 20 Years of Data"

FILES = [
    ROOT / "ipodata" / "finalipodata_expanded_20yr.xlsx",
    ROOT / "data" / "cg_issue.csv",
    ROOT / "data" / "cg_subs.csv",
    ROOT / "data" / "cg_listing.csv",
    ROOT / "data" / "ipo_outcomes.csv",
]

DATASET_README = """\
# India Mainboard IPOs: 20 Years of Data

NSE/BSE **mainboard** IPOs from 2004 onward — issue structure, category-wise
subscription, listing-day prices, and post-listing price paths out to 24 months.
Refreshed monthly from a self-updating pipeline
(https://github.com/rohanbeingsocial/rhp-analyst) that pulls Chittorgarh's IPO
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


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    print(out.strip())
    return proc


def main() -> int:
    user = os.environ.get("KAGGLE_USERNAME", "").strip()
    if not user or not os.environ.get("KAGGLE_KEY", "").strip():
        print("KAGGLE_USERNAME / KAGGLE_KEY not set - skipping publish")
        return 0

    missing = [str(p) for p in FILES if not p.exists()]
    if missing:
        print("missing dataset files:", ", ".join(missing))
        return 1

    with open(ROOT / "data" / "cg_issue.csv", encoding="utf-8", newline="") as f:
        n_ipos = sum(1 for _ in f) - 1

    stage = Path(tempfile.mkdtemp(prefix="kaggle_stage_"))
    for p in FILES:
        shutil.copy2(p, stage / p.name)
    (stage / "README.md").write_text(DATASET_README, encoding="utf-8")
    meta = {"title": TITLE, "id": f"{user}/{SLUG}", "licenses": [{"name": "other"}]}
    (stage / "dataset-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    msg = f"auto refresh {datetime.now(timezone.utc):%Y-%m-%d} ({n_ipos} IPOs)"
    ver = run(["kaggle", "datasets", "version", "-p", str(stage), "-m", msg])
    if ver.returncode == 0:
        print(f"published new version of {user}/{SLUG}: {msg}")
        return 0

    blob = ((ver.stdout or "") + (ver.stderr or "")).lower()
    if "404" in blob or "not found" in blob:
        crt = run(["kaggle", "datasets", "create", "-p", str(stage), "--public"])
        if crt.returncode == 0:
            print(f"created dataset kaggle.com/datasets/{user}/{SLUG}")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
