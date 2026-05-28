"""
session_journal.py  —  GRU session logging module

Logs each reviewed deal to session_logs/journal_YYYYMMDD.json.
Each file is a JSON array of entry objects.

Usage (from app 7.py or standalone):
    import session_journal
    session_journal.log_entry(deal_name, verdict, metrics, ri_signals, note)
    entries = session_journal.read_recent(n=10)
"""
import datetime
import json
import pathlib
from typing import Optional

LOG_DIR = pathlib.Path(__file__).parent / "session_logs"


def log_entry(
    deal_name: str,
    verdict: str,
    metrics: dict,
    ri_signals: Optional[dict] = None,
    note: str = "",
) -> pathlib.Path:
    """Append one entry to today's journal file. Returns the path written."""
    LOG_DIR.mkdir(exist_ok=True)
    today = datetime.date.today().strftime("%Y%m%d")
    log_path = LOG_DIR / f"journal_{today}.json"

    existing: list = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []

    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "deal_name": deal_name,
        "verdict": verdict,
        "metrics": {
            "giy":     _safe_round(metrics.get("giy")),
            "niy":     _safe_round(metrics.get("niy")),
            "dscr":    _safe_round(metrics.get("dscr")),
            "icr":     _safe_round(metrics.get("icr")),
            "ltv":     _safe_round(metrics.get("ltv")),
            "wault":   _safe_round(metrics.get("wault")),
            "vacancy": _safe_round(metrics.get("vacancy_rate")),
            "noi":     _safe_round(metrics.get("noi_proxy"), 0),
            "asking":  _safe_round(metrics.get("asking_price"), 0),
        },
        "ri_signals": ri_signals or {},
        "note": note.strip(),
    }
    existing.append(entry)
    log_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return log_path


def read_recent(n: int = 10) -> list:
    """Return the last n entries across all journal files, chronologically."""
    LOG_DIR.mkdir(exist_ok=True)
    all_entries: list = []
    for f in sorted(LOG_DIR.glob("journal_*.json")):
        try:
            batch = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(batch, list):
                all_entries.extend(batch)
        except Exception:
            pass
    return all_entries[-n:]


def _safe_round(v, decimals: int = 4):
    if v is None:
        return None
    try:
        return round(float(v), decimals)
    except (TypeError, ValueError):
        return None
