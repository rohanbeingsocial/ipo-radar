"""Optional AI layer (Anthropic API, or Claude Code CLI on a subscription).

Strict contract: scores stay deterministic. The LLM may only
  1. rewrite the executive summary / bull-bear cases into fluent prose,
     constrained to the evidence items we supply,
  2. run the narrative-vs-numbers consistency check (MD&A claims vs extracted
     financials),
  3. answer Q&A questions with citations from retrieved section text.
Without a configured provider every feature degrades to deterministic output.

Providers (config.LLM_PROVIDER):
  api        — Anthropic SDK with ANTHROPIC_API_KEY (metered).
  claude_cli — `claude -p` headless; billing rides the machine's logged-in
               Claude subscription. No credentials are read or stored here;
               the CLI handles its own auth (optionally pinned to an account
               via CLAUDE_CLI_CONFIG_DIR → CLAUDE_CONFIG_DIR).
  auto       — claude_cli when the CLI is installed, else api when a key is set.
"""
from __future__ import annotations

import json
import os
import re
import shutil as _shutil
import subprocess

from ..config import (ANTHROPIC_API_KEY, ANTHROPIC_MODEL, CLAUDE_CLI_BIN,
                      CLAUDE_CLI_CONFIG_DIR, CLAUDE_CLI_MODEL, CLAUDE_CLI_TIMEOUT,
                      LLM_PROVIDER)


def _cli_path() -> str | None:
    return _shutil.which(CLAUDE_CLI_BIN)


def _provider() -> str | None:
    if LLM_PROVIDER == "api":
        return "api" if ANTHROPIC_API_KEY else None
    if LLM_PROVIDER == "claude_cli":
        return "claude_cli" if _cli_path() else None
    # auto: prefer the subscription CLI (no metered cost), fall back to the API
    if _cli_path():
        return "claude_cli"
    if ANTHROPIC_API_KEY:
        return "api"
    return None


def llm_available() -> bool:
    return _provider() is not None


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _ask_api(system: str, user: str, max_tokens: int) -> str | None:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None
    try:
        client = _client()
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            return None
        return next((b.text for b in response.content if b.type == "text"), None)
    except anthropic.APIError:
        return None
    except Exception:
        return None


def _ask_cli(system: str, user: str) -> str | None:
    """Headless Claude Code: prompt on stdin, plain-text answer on stdout.
    Runs with all tools disabled — this is a pure text completion."""
    exe = _cli_path()
    if not exe:
        return None
    env = os.environ.copy()
    if CLAUDE_CLI_CONFIG_DIR:
        env["CLAUDE_CONFIG_DIR"] = CLAUDE_CLI_CONFIG_DIR
    try:
        proc = subprocess.run(
            [exe, "-p", "--model", CLAUDE_CLI_MODEL, "--output-format", "text",
             "--tools", "", "--append-system-prompt", system],
            input=user, capture_output=True, text=True, encoding="utf-8",
            timeout=CLAUDE_CLI_TIMEOUT, env=env,
        )
        out = (proc.stdout or "").strip()
        return out if proc.returncode == 0 and out else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def _ask(system: str, user: str, max_tokens: int = 2000) -> str | None:
    provider = _provider()
    if provider == "claude_cli":
        return _ask_cli(system, user)
    if provider == "api":
        return _ask_api(system, user, max_tokens)
    return None


SYSTEM_ANALYST = (
    "You are a financial-document analyst assisting with an automated RHP (Indian IPO "
    "prospectus) analysis tool. You must ONLY use the facts and evidence provided in the "
    "user message — never invent numbers, names, or claims. Keep page citations in the "
    "form [p.N] attached to the facts they came from. Neutral, factual tone. This is "
    "research, never investment advice; do not recommend applying to or avoiding the IPO."
)


def enhance_report(report: dict) -> dict:
    """Rewrite executive summary + bull/bear cases as fluent prose. Returns the
    (possibly) updated report; on any failure the deterministic text stands."""
    if not llm_available():
        return report

    facts = {
        "executive_summary": report.get("executive_summary"),
        "bull": [c["text"] for c in report.get("cases", {}).get("bull", [])],
        "bear": [c["text"] for c in report.get("cases", {}).get("bear", [])],
        "verdict": report.get("verdict"),
        "overall_score": report.get("scoring", {}).get("overall"),
    }
    prompt = (
        "Rewrite the following extracted findings into (1) a 3-paragraph executive summary, "
        "(2) a bull case of 4-6 bullets, (3) a bear case of 4-6 bullets. Preserve every "
        "number and [p.N] citation exactly. Do not add any claim not present in the input.\n"
        "Return JSON: {\"executive_summary\": [\"para1\", ...], \"bull\": [...], \"bear\": [...]}\n\n"
        + json.dumps(facts, ensure_ascii=False, default=str)
    )
    text = _ask(SYSTEM_ANALYST, prompt, max_tokens=3000)
    if not text:
        return report
    try:
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0) if m else text)
        if data.get("executive_summary"):
            report["executive_summary"] = [
                {"text": p, "source_pages": []} for p in data["executive_summary"]
            ]
        for side in ("bull", "bear"):
            if data.get(side):
                report["cases"][side] = [
                    {"text": t, "rule": "llm_narrative", "category": side,
                     "source_pages": _extract_pages(t), "confidence": 0.7}
                    for t in data[side]
                ]
        report["meta"]["llm_enhanced"] = True
    except (json.JSONDecodeError, KeyError, AttributeError):
        pass
    return report


def consistency_check(mdna_text: str, financial_facts: dict) -> list[dict]:
    """Narrative-vs-numbers: does the MD&A's story match the restated numbers?
    Deterministic fallback: growth adjectives vs actual CAGR."""
    findings: list[dict] = []
    rev_cagr = financial_facts.get("revenue_cagr")
    if rev_cagr is not None and re.search(r"(?:strong|robust|significant|substantial)\s+growth", mdna_text[:30000], re.I):
        if rev_cagr < 0.08:
            findings.append({
                "type": "narrative_mismatch", "severity": "medium",
                "detail": f"MD&A describes growth as strong/robust while extracted revenue CAGR is "
                          f"{rev_cagr * 100:.1f}% — verify which period management refers to.",
            })
    if not llm_available() or not mdna_text:
        return findings

    prompt = (
        "Compare the management narrative excerpt against the extracted financial facts. "
        "List up to 4 specific inconsistencies (claims not supported by the numbers) as JSON: "
        "[{\"detail\": str, \"severity\": \"low|medium|high\"}]. If none, return [].\n\n"
        f"FACTS: {json.dumps(financial_facts, default=str)}\n\nMD&A EXCERPT:\n{mdna_text[:12000]}"
    )
    text = _ask(SYSTEM_ANALYST, prompt, max_tokens=1500)
    if text:
        try:
            m = re.search(r"\[.*\]", text, re.S)
            for item in json.loads(m.group(0) if m else text):
                if isinstance(item, dict) and item.get("detail"):
                    findings.append({"type": "narrative_mismatch",
                                     "severity": item.get("severity", "low"),
                                     "detail": str(item["detail"])[:500]})
        except (json.JSONDecodeError, AttributeError):
            pass
    return findings[:6]


def answer_question(question: str, sections: list[dict]) -> dict:
    """Cited Q&A over retrieved section excerpts ('Talk to EDGAR' pattern)."""
    if not llm_available():
        return {"answer": None, "error": "AI layer not configured. Set ANTHROPIC_API_KEY, or install "
                                         "Claude Code and set LLM_PROVIDER=claude_cli to use a Claude subscription."}

    corpus = _retrieve(question, sections)
    if not corpus:
        return {"answer": "No relevant sections were extracted from this document for that question.",
                "citations": []}
    context = "\n\n".join(
        f"[SECTION: {c['title']} | pages {c['page_start']}-{c['page_end']}]\n{c['excerpt']}"
        for c in corpus)
    prompt = (
        "Answer the question using ONLY the prospectus excerpts below. Quote the supporting "
        "sentence(s) and cite pages as [p.N] (pages are given per section). If the excerpts "
        "don't contain the answer, say so explicitly.\n\n"
        f"QUESTION: {question}\n\nEXCERPTS:\n{context}"
    )
    text = _ask(SYSTEM_ANALYST, prompt, max_tokens=1500)
    return {"answer": text or "The AI layer could not produce an answer.",
            "citations": [{"section": c["title"], "pages": [c["page_start"], c["page_end"]]} for c in corpus]}


def _retrieve(question: str, sections: list[dict], top_k: int = 4) -> list[dict]:
    """Keyword retrieval over section texts (embedding upgrade is roadmap M3)."""
    terms = [w for w in re.findall(r"[a-z]{3,}", question.lower())
             if w not in {"the", "and", "what", "which", "does", "how", "are", "was", "were", "this", "that", "company"}]
    scored = []
    for sec in sections:
        text = (sec.get("text") or "")[:120000].lower()
        if not text:
            continue
        score = sum(text.count(t) for t in terms)
        if score > 0:
            scored.append((score, sec))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, sec in scored[:top_k]:
        text = sec.get("text") or ""
        pos = min((text.lower().find(t) for t in terms if text.lower().find(t) >= 0), default=0)
        start = max(0, pos - 500)
        out.append({"title": sec.get("title"), "page_start": sec.get("page_start"),
                    "page_end": sec.get("page_end"), "excerpt": text[start:start + 4000]})
    return out


def _extract_pages(text: str) -> list[int]:
    return [int(p) for p in re.findall(r"\[p\.(\d+)\]", text)][:5]
