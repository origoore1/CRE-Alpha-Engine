#!/usr/bin/env python3
"""
ai_monitor.py  —  GRU AI Capabilities Monitor
==============================================
Searches for recent Claude/Anthropic updates and AI tools relevant to
real estate underwriting. Generates a structured report saved to ai_updates/.

Usage:
    python ai_monitor.py              # run now, skip if report exists today
    python ai_monitor.py --force      # always regenerate
    python ai_monitor.py --quiet      # suppress console output

Dependencies (auto-installed if missing):
    duckduckgo_search, feedparser, requests
"""

import os
import re
import sys
import ssl
import json
import time
import pathlib
import argparse
import datetime
import textwrap
import subprocess
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# ── optional libs ─────────────────────────────────────────────────────────────
try:
    from duckduckgo_search import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False

try:
    import feedparser
    HAS_FP = True
except ImportError:
    HAS_FP = False

try:
    import requests as _req
    HAS_REQ = True
except ImportError:
    HAS_REQ = False


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

OUTPUT_DIR = pathlib.Path(__file__).parent / "ai_updates"

VERSION    = "1.0"
TODAY      = datetime.date.today()
DATE_STR   = TODAY.strftime("%Y-%m-%d")
DISPLAY_DT = TODAY.strftime("%d %B %Y")

RSS_SOURCES = [
    ("Anthropic Blog",       "https://www.anthropic.com/rss.xml"),
    ("Anthropic News (alt)", "https://www.anthropic.com/news.rss"),
]

DDGS_QUERIES = [
    ("Claude model updates",         "Anthropic Claude new model features release 2025 2026",             6),
    ("Claude API capabilities",      "Claude API new capabilities tool use extended thinking 2025 2026",  5),
    ("AI CRE tools",                 "AI tools commercial real estate underwriting analysis 2025 2026",   5),
    ("AI financial analysis",        "AI machine learning real estate financial modelling 2025",          4),
]

# ── GRU relevance notes ────────────────────────────────────────────────────────
# keyword (lower) → one-line note for the analyst
GRU_NOTES: dict[str, str] = {
    "claude opus 4":         "Most capable reasoning — stronger SWOT narratives and scenario analysis in Deep Insights",
    "claude sonnet 4":       "Current production model used in Claude Export; updated version = better lease abstraction",
    "claude haiku":          "Fast/cheap batch mode — could screen all 12 practice deals overnight at minimal cost",
    "extended thinking":     "Deep reasoning chains → richer covenant analysis and risk narrative generation in IC memos",
    "computer use":          "Could automate Excel template population directly from broker PDFs — replaces intake_deal.py",
    "tool use":              "GRU could expose its engine as callable Claude tools (DSCR calc, QA audit, verdict engine)",
    "vision":                "Solves scanned-PDF gap in DD Scanner — 'Manual review required' cells would largely disappear",
    "pdf":                   "Native PDF ingestion → direct lease abstraction without pdfplumber regex layer",
    "200k context":          "Full rent roll + all lease PDFs + market comparables fit in a single analysis prompt",
    "context window":        "Larger context = entire deal pack (rent roll + leases + cost sheet) in one prompt",
    "web search":            "Live ERV and market yield data could fill the documented 'no live ERV data' gap in Module 4",
    "structured output":     "JSON mode → standardise Claude Export format; downstream parsing becomes trivial",
    "batch api":             "Overnight batch processing of all practice deals for regression testing and audit trails",
    "prompt caching":        "Cache GRU system prompt across sessions → cheaper repeat analysis of same deal parameters",
    "multilingual":          "Improved German → lease clause extraction in DD Scanner would increase field coverage",
    "fine-tuning":           "GRU-specific fine-tune on German retail deals — bespoke covenant intelligence beyond ANCHOR_NAMES",
    "agent":                 "Autonomous screener: ingest broker PDF → populate Excel template → return IC memo draft",
    "multi-agent":           "Parallel sub-agents: rent roll analyser + lease scanner + market data fetcher → merged verdict",
    "retrieval":             "RAG over Lahav deal history → closest comparable transaction in SWOT Opportunities section",
    "embeddings":            "Semantic similarity search across portfolio deals for rent benchmarking and WAULT comparison",
    "real estate":           "Pre-trained CRE benchmarks → sharper yield and vacancy commentary beyond hardcoded thresholds",
    "financial":             "Better numeric reasoning → fewer errors in DSCR/IRR re-calculation in Claude chat follow-ups",
    "code interpreter":      "Analysts run GRU engine calculations inside Claude conversation without leaving browser",
    "mcp":                   "MCP server wrapping GRU functions → ask Claude Desktop 'analyse Deal 12' by name",
    "model context protocol":"GRU app.py could register as MCP tool; Claude Code sessions auto-load project context",
    "claude code":           "Directly used to build GRU; longer sessions + improved context = fewer re-explorations",
    "api":                   "Direct API integration → GRU calls Claude for narrative generation of IC rationale",
    "claude.ai":             "Projects feature stores GRU context permanently — no re-uploading CLAUDE.md each session",
    "projects":              "Persistent project memory in Claude.ai — store GRU architecture as permanent context",
    "voice":                 "Voice input could allow analysts to dictate lease terms for intake — hands-free workflow",
    "computer vision":       "Same as vision — scanned lease extraction for DD Scanner",
    "intellcre":             "Direct competitor context: AI-native CRE underwriting; benchmark against GRU feature set",
    "builtai":               "AI acquisition and portfolio management tool; comparable to GRU Portfolio tab",
    "feasibly":              "Multi-agent real estate feasibility tool; architecture similar to proposed GRU agent layer",
    "lease abstraction":     "Validates DD Scanner approach; confirms market need for automated term extraction",
    "covenant":              "Covenant monitoring tools validate GRU QA audit design; check for threshold benchmarks",
    "escrow":                "German CRE escrow analytics — potential integration point for acquisition workflow",
}


# ═══════════════════════════════════════════════════════════════════════════════
# FETCH HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_fetch(url: str, timeout: int = 10) -> str:
    """Fetch URL text; return empty string on any error."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 GRU-AI-Monitor/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _fetch_rss(url: str, source_name: str, max_items: int = 8) -> list[dict]:
    """Parse an RSS/Atom feed; return list of {title, link, date, summary}."""
    items = []
    raw   = _safe_fetch(url)
    if not raw:
        return items

    if HAS_FP:
        feed = feedparser.parse(raw)
        for entry in feed.entries[:max_items]:
            date_str = ""
            if hasattr(entry, "published"):
                date_str = entry.published[:10]
            elif hasattr(entry, "updated"):
                date_str = entry.updated[:10]
            items.append({
                "source":  source_name,
                "title":   getattr(entry, "title",   "(no title)"),
                "link":    getattr(entry, "link",    ""),
                "date":    date_str,
                "summary": re.sub(r"<[^>]+>", "", getattr(entry, "summary", ""))[:200],
            })
    else:
        # Minimal stdlib XML fallback
        try:
            root = ET.fromstring(raw)
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            # RSS 2.0
            for item in root.iter("item"):
                title   = (item.findtext("title")       or "(no title)").strip()
                link    = (item.findtext("link")        or "").strip()
                date    = (item.findtext("pubDate")     or "")[:16]
                summary = re.sub(r"<[^>]+>",
                                 "",
                                 item.findtext("description") or "")[:200]
                items.append({"source": source_name, "title": title,
                              "link": link, "date": date, "summary": summary})
                if len(items) >= max_items:
                    break
            # Atom fallback
            if not items:
                for entry in list(root.iter("{http://www.w3.org/2005/Atom}entry"))[:max_items]:
                    title   = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
                    link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                    link    = (link_el.get("href", "") if link_el is not None else "")
                    date    = (entry.findtext("{http://www.w3.org/2005/Atom}updated") or "")[:10]
                    summary = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "")[:200]
                    items.append({"source": source_name, "title": title,
                                  "link": link, "date": date, "summary": summary})
        except ET.ParseError:
            pass
    return items


def _search_ddgs(query: str, max_results: int = 6) -> list[dict]:
    """Search with DuckDuckGo; return list of {title, href, body}."""
    if not HAS_DDGS:
        return []
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "link":  r.get("href",  ""),
                    "body":  r.get("body",  "")[:200],
                })
                time.sleep(0.15)   # be polite
    except Exception:
        pass
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# RELEVANCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _gru_notes_for(text: str) -> list[str]:
    """Return GRU impact notes for any keyword found in text."""
    tl   = text.lower()
    seen: set[str] = set()
    out:  list[str] = []
    for kw, note in GRU_NOTES.items():
        if kw in tl and note not in seen:
            seen.add(note)
            out.append(f"[{kw}]  {note}")
    return out


def _dedupe(items: list[dict], key: str = "title") -> list[dict]:
    seen: set[str] = set()
    out:  list[dict] = []
    for item in items:
        k = item.get(key, "").lower()[:60]
        if k and k not in seen:
            seen.add(k)
            out.append(item)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

SEP  = "=" * 72
SEP2 = "-" * 72

def _wrap(text: str, width: int = 70, indent: str = "  ") -> str:
    return textwrap.fill(text, width=width, initial_indent=indent,
                         subsequent_indent=indent)


def build_report(
    rss_items:      list[dict],
    ddgs_claude:    list[dict],
    ddgs_cre:       list[dict],
) -> str:
    lines = []

    def H(t):  lines.append(f"\n{SEP}\n  {t}\n{SEP}")
    def H2(t): lines.append(f"\n{t}\n{SEP2}")
    def KV(k, v): lines.append(f"  {k:<28}: {v}")
    def LI(s): lines.append(f"    •  {s}")
    def BR():  lines.append("")

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append(SEP)
    lines.append("  AI CAPABILITIES UPDATE")
    lines.append("  What's New and How It Applies to GRU")
    lines.append(f"  Generated: {DISPLAY_DT}  |  ai_monitor.py v{VERSION}")
    lines.append(SEP)
    lines.append("")
    lines.append("  GRU = German Retail Underwriter (Lahav L.R. internal tool)")
    lines.append("  This report is auto-generated. Verify all links before acting.")

    # ── Section 1: Anthropic / Claude updates ─────────────────────────────────
    H("SECTION 1 · ANTHROPIC / CLAUDE UPDATES")
    H2("RSS Feed Items")

    if rss_items:
        for item in rss_items[:10]:
            BR()
            lines.append(f"  [{item.get('date','?')}]  {item.get('title','?')}")
            if item.get("link"):
                lines.append(f"  {item['link']}")
            if item.get("summary", "").strip():
                lines.append(_wrap(item["summary"].strip()))
    else:
        lines.append("  (RSS feeds unavailable or returned no items)")

    H2("Search Results — Claude & Anthropic")
    if ddgs_claude:
        for r in ddgs_claude[:8]:
            BR()
            lines.append(f"  {r.get('title','?')}")
            if r.get("link"):
                lines.append(f"  {r['link']}")
            if r.get("body", "").strip():
                lines.append(_wrap(r["body"].strip()))
    else:
        lines.append("  (No search results returned)")

    # ── Section 2: AI tools for CRE ───────────────────────────────────────────
    H("SECTION 2 · AI TOOLS FOR REAL ESTATE & FINANCIAL ANALYSIS")

    if ddgs_cre:
        for r in ddgs_cre[:8]:
            BR()
            lines.append(f"  {r.get('title','?')}")
            if r.get("link"):
                lines.append(f"  {r['link']}")
            if r.get("body", "").strip():
                lines.append(_wrap(r["body"].strip()))
    else:
        lines.append("  (No search results returned)")

    # ── Section 3: GRU impact assessment ──────────────────────────────────────
    H("SECTION 3 · GRU IMPACT ASSESSMENT")
    lines.append("")
    lines.append("  For each capability found across Sections 1 and 2,")
    lines.append("  one-line note on whether/how it could improve GRU.")
    lines.append("")

    # Collect all text for keyword matching
    all_text = " ".join([
        item.get("title","") + " " + item.get("summary","")
        for item in rss_items
    ] + [
        r.get("title","") + " " + r.get("body","")
        for r in ddgs_claude + ddgs_cre
    ])

    gru_impacts = _gru_notes_for(all_text)

    if gru_impacts:
        for note in gru_impacts:
            LI(note)
    else:
        lines.append("  No directly GRU-relevant capabilities detected in this run.")
        lines.append("  This may indicate a quiet period or network/search issues.")

    # ── Section 4: Always-relevant baseline ───────────────────────────────────
    H("SECTION 4 · STANDING RECOMMENDATIONS")
    lines.append("")
    lines.append("  These apply regardless of new announcements:")
    BR()
    LI("CLAUDE.md context  — keep it updated as GRU evolves; Claude Code sessions")
    lines.append("                       use it as ground truth and save ~15 min re-exploration each time")
    BR()
    LI("Export to Claude   — test every new model release against the 12 practice deals;")
    lines.append("                       copy Claude Export → new chat → ask 'which deal should I buy?'")
    BR()
    LI("DD Scanner gap     — scanned PDFs still return 'Manual review required'; top priority")
    lines.append("                       fix is a Vision API or native PDF ingestion layer")
    BR()
    LI("ERV data gap       — Module 4 (True Yield Gap) uses heuristics; live market data API")
    lines.append("                       (gif.de, bulwiengesa, or JLL API) would make it institutional-grade")
    BR()
    LI("MCP integration    — GRU engine functions (run_qa_audit, generate_verdict) could be")
    lines.append("                       exposed as MCP tools for Claude Desktop — analysts ask in plain language")

    # ── Footer ────────────────────────────────────────────────────────────────
    lines.append(f"\n{SEP}")
    lines.append(f"  End of report  |  Next run: {(TODAY + datetime.timedelta(days=7)).strftime('%d %B %Y')}")
    lines.append(f"  ai_monitor.py v{VERSION}  |  {DATE_STR}")
    lines.append(SEP)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def latest_report_path() -> pathlib.Path | None:
    """Return path to the most recently generated report, or None."""
    if not OUTPUT_DIR.exists():
        return None
    reports = sorted(OUTPUT_DIR.glob("ai_update_*.txt"), reverse=True)
    return reports[0] if reports else None


def run(force: bool = False, quiet: bool = False) -> pathlib.Path:
    """
    Run the monitor. Returns path to the saved report.
    Skips execution if a report already exists for today (unless force=True).
    """
    OUTPUT_DIR.mkdir(exist_ok=True)

    out_path = OUTPUT_DIR / f"ai_update_{DATE_STR}.txt"

    if out_path.exists() and not force:
        if not quiet:
            print(f"[ai_monitor] Report for {DATE_STR} already exists: {out_path}")
            print("  Use --force to regenerate.")
        return out_path

    if not quiet:
        print(f"[ai_monitor] Starting AI capabilities scan — {DISPLAY_DT}")

    # ── Step 1: RSS feeds ──────────────────────────────────────────────────────
    rss_items: list[dict] = []
    for name, url in RSS_SOURCES:
        if not quiet:
            print(f"  Fetching RSS: {name} …", end=" ", flush=True)
        items = _fetch_rss(url, name)
        if not quiet:
            print(f"{len(items)} items")
        rss_items.extend(items)
    rss_items = _dedupe(rss_items)

    # ── Step 2: DDGS — Claude / Anthropic ─────────────────────────────────────
    ddgs_claude: list[dict] = []
    ddgs_cre:    list[dict] = []

    if HAS_DDGS:
        claude_queries = [q for label, q, n in DDGS_QUERIES if "claude" in label.lower()
                          or "api" in label.lower()]
        cre_queries    = [q for label, q, n in DDGS_QUERIES if "cre" in label.lower()
                          or "financial" in label.lower()]

        for q in claude_queries:
            if not quiet:
                print(f"  Searching: {q[:55]}…", end=" ", flush=True)
            res = _search_ddgs(q, max_results=6)
            if not quiet:
                print(f"{len(res)} results")
            ddgs_claude.extend(res)
            time.sleep(0.5)

        for q in cre_queries:
            if not quiet:
                print(f"  Searching: {q[:55]}…", end=" ", flush=True)
            res = _search_ddgs(q, max_results=5)
            if not quiet:
                print(f"{len(res)} results")
            ddgs_cre.extend(res)
            time.sleep(0.5)

        ddgs_claude = _dedupe(ddgs_claude)
        ddgs_cre    = _dedupe(ddgs_cre)
    else:
        if not quiet:
            print("  [WARN] duckduckgo_search not installed — skipping web search")
            print("         Run: pip install duckduckgo_search")

    # ── Step 3: Build and save report ─────────────────────────────────────────
    if not quiet:
        print("  Building report…", end=" ", flush=True)

    report = build_report(rss_items, ddgs_claude, ddgs_cre)

    out_path.write_text(report, encoding="utf-8")

    if not quiet:
        print(f"saved.")
        print(f"\n[ai_monitor] Report saved: {out_path}")
        print(f"  RSS items:     {len(rss_items)}")
        print(f"  Claude search: {len(ddgs_claude)} results")
        print(f"  CRE search:    {len(ddgs_cre)} results")

    # ── Step 4: Also save a "latest.txt" symlink/copy ─────────────────────────
    latest_path = OUTPUT_DIR / "latest.txt"
    latest_path.write_text(report, encoding="utf-8")

    return out_path


def read_latest() -> str:
    """Return text of the most recent report, or an info string."""
    p = latest_report_path()
    if p is None:
        return "No report found. Run: python ai_monitor.py"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"Could not read report: {e}"


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GRU AI Capabilities Monitor — generate weekly AI update report"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerate report even if one exists for today",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress progress output",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Print the latest report to stdout without regenerating",
    )
    args = parser.parse_args()

    if args.show:
        print(read_latest())
    else:
        path = run(force=args.force, quiet=args.quiet)
        print(f"\n--- Report preview (first 60 lines) ---")
        lines = path.read_text(encoding="utf-8").splitlines()
        preview = "\n".join(lines[:60])
        # Safe print for Windows consoles with narrow encodings (cp1255, cp1252, etc.)
        sys.stdout.buffer.write((preview + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
        if len(lines) > 60:
            print(f"\n  ... {len(lines)-60} more lines -- open {path} for full report")
