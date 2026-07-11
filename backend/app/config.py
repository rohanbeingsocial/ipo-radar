import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# PostgreSQL in production (set DATABASE_URL=postgresql+psycopg2://user:pass@host/db),
# SQLite fallback so the app runs with zero configuration.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'rhp.db').as_posix()}")

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "300"))

# Optional AI layer. All scoring is deterministic; the LLM only writes narrative
# text and answers Q&A, constrained to extracted evidence.
#
# Two providers:
#   LLM_PROVIDER=api         Anthropic API via ANTHROPIC_API_KEY (metered billing)
#   LLM_PROVIDER=claude_cli  Claude Code CLI in headless mode — uses the machine's
#                            logged-in Claude subscription (Pro/Max), no API key.
#   LLM_PROVIDER=auto        (default) claude_cli if configured+installed, else api
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto").lower()
CLAUDE_CLI_BIN = os.getenv("CLAUDE_CLI_BIN", "claude")
# Model alias for the CLI ("sonnet"/"opus"/full id). Pro subscriptions include Sonnet.
CLAUDE_CLI_MODEL = os.getenv("CLAUDE_CLI_MODEL", "sonnet")
# Optional CLAUDE_CONFIG_DIR for the subprocess, to pin a specific logged-in
# account (e.g. a dedicated dashboard account) instead of the default one.
CLAUDE_CLI_CONFIG_DIR = os.getenv("CLAUDE_CLI_CONFIG_DIR", "")
CLAUDE_CLI_TIMEOUT = int(os.getenv("CLAUDE_CLI_TIMEOUT", "180"))

# OCR is opt-in: most mainboard RHPs are born-digital.
ENABLE_OCR = os.getenv("ENABLE_OCR", "0") == "1"

# The GitHub Pages demo is allowed by default so a locally running engine can
# power its upload/analyze mode without extra configuration.
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,https://rohanbeingsocial.github.io",
).split(",")

DISCLAIMER = (
    "This is an automated document analysis for research and education. It is not "
    "investment advice, not a recommendation, and not a SEBI-registered research report. "
    "Scores reflect only information inside the uploaded prospectus and stated assumptions."
)
