"""
screener.py -- SAE Batch Screener
Screens a list of tickers, saves results to CSV, prints top stocks.

Usage:
    python screener.py                  # screens sp500.txt
    python screener.py my_list.txt      # screens custom file
    python screener.py --top            # prints best buys from last results.csv
"""

import sys
import os
import time
import csv
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from data_fetcher import fetch, DataFetchError
from ratio_calculator import calculate
from scorer import score

# Sectors to skip -- our scoring model doesn't apply to these
SKIP_SECTORS = {"Financial Services", "Banks", "Insurance"}

# Tickers to always skip (pure banks/insurers in S&P 500)
SKIP_TICKERS = {
    "JPM", "BAC", "WFC", "C", "GS", "MS", "USB", "PNC", "TFC", "COF",
    "MTB", "CFG", "HBAN", "KEY", "RF", "STT", "BK", "SCHW", "AXP",
    "BRK-B", "MET", "PRU", "AFL", "ALL", "AIG", "CB", "TRV", "HIG",
    "PGR", "CINF", "AIZ", "GL", "BEN", "IVZ", "TROW", "BLK",
}

CSV_FIELDS = [
    "ticker", "name", "sector", "score", "classification",
    "roic", "revenue_cagr_3yr", "gross_margin", "operating_margin",
    "net_debt_to_ebitda", "fcf_margin", "dcf_upside_pct",
    "current_price", "dcf_intrinsic_value", "shares_outstanding",
    "red_flag_count", "bottom_line", "run_date"
]


def _fmt(v, pct=False):
    if v is None:
        return "N/A"
    if pct:
        return f"{v*100:.1f}%"
    return f"{v:.2f}"


def _m(v):
    if v is None:
        return "N/A"
    if abs(v) >= 1e12:
        return f"${v/1e12:.1f}T"
    if abs(v) >= 1e9:
        return f"${v/1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:.0f}"


def _print_best_buys(results):
    """Print only stocks where intrinsic value > current price, sorted by score+upside."""
    candidates = [
        r for r in results
        if r["score"] >= 65
        and r.get("dcf_upside_pct") is not None
        and r["dcf_upside_pct"] > 0
    ]

    # Sort: score is primary (70%), upside breaks ties (30%)
    candidates.sort(
        key=lambda x: x["score"] * 0.7 + x["dcf_upside_pct"] * 100 * 0.3,
        reverse=True
    )

    if not candidates:
        print("\n  No stocks meet criteria (score >= 65 AND intrinsic > price).")
        return

    print(f"\n  BEST BUYS -- score >= 65 AND intrinsic > price  ({len(candidates)} found)")
    print(f"  Sorted: quality score (70%) + upside potential (30%)")
    print()
    hdr = f"  {'#':<4} {'Ticker':<7} {'Score':<7} {'Classification':<22} {'Price':>8} {'IV (DCF)':>9} {'Upside':>8} {'Value Gap'}"
    print(hdr)
    print("  " + "-" * 82)
    for rank, r in enumerate(candidates, 1):
        price  = r.get("current_price")
        iv     = r.get("dcf_intrinsic_value")
        upside = r.get("dcf_upside_pct")
        shares = r.get("shares_outstanding") or 0
        gap_total = (iv - price) * shares if (iv and price and shares) else None

        price_str  = f"${price:.2f}" if price else "N/A"
        iv_str     = f"${iv:.2f}"    if iv    else "N/A"
        upside_str = f"+{upside*100:.1f}%" if upside else "N/A"
        gap_str    = _m(gap_total)   if gap_total else ""

        print(
            f"  {rank:<4} {r['ticker']:<7} {r['score']:<7.1f} "
            f"{r['classification']:<22} "
            f"{price_str:>8} "
            f"{iv_str:>9} "
            f"{upside_str:>8}  "
            f"{gap_str}"
        )


def screen(ticker_file="sp500.txt", output_csv="results.csv", delay=1.2):
    base = os.path.dirname(__file__)
    ticker_path = os.path.join(base, ticker_file)

    with open(ticker_path) as f:
        tickers = [t.strip() for t in f if t.strip() and not t.startswith("#")]

    total   = len(tickers)
    results = []
    skipped = []
    errors  = []

    print(f"\nSAE Batch Screener -- {total} tickers")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 62)

    for i, ticker in enumerate(tickers, 1):
        if ticker in SKIP_TICKERS:
            print(f"  [{i:>3}/{total}] {ticker:<6}  SKIPPED (bank/insurer)")
            skipped.append(ticker)
            continue

        print(f"  [{i:>3}/{total}] {ticker:<6}  fetching...", end="", flush=True)

        try:
            data   = fetch(ticker)
            sector = data.get("sector", "")

            if sector in SKIP_SECTORS:
                print(f"  SKIPPED ({sector})")
                skipped.append(ticker)
                continue

            ratios = calculate(data)
            scores = score(ratios, data)

            m1    = scores["module1_score"]
            label = scores["classification"]
            flags = len(scores["red_flags"])

            upside = ratios.get("dcf_upside_pct")
            if upside is None:
                dcf_sig = "unknown"
            elif upside > 0.15:
                dcf_sig = "undervalued"
            elif upside > -0.15:
                dcf_sig = "fair"
            else:
                dcf_sig = "overvalued"

            results.append({
                "ticker":             ticker,
                "name":               data.get("name", ticker),
                "sector":             sector,
                "score":              m1,
                "classification":     label,
                "roic":               ratios.get("roic"),
                "revenue_cagr_3yr":   ratios.get("revenue_cagr_3yr"),
                "gross_margin":       ratios.get("gross_margin"),
                "operating_margin":   ratios.get("operating_margin"),
                "net_debt_to_ebitda": ratios.get("net_debt_to_ebitda"),
                "fcf_margin":         ratios.get("fcf_margin"),
                "dcf_upside_pct":     upside,
                "current_price":      data.get("current_price"),
                "dcf_intrinsic_value":ratios.get("dcf_intrinsic_value"),
                "shares_outstanding": data.get("shares_outstanding"),
                "red_flag_count":     flags,
                "bottom_line":        f"{label} / DCF: {dcf_sig}",
                "run_date":           datetime.now().strftime("%Y-%m-%d"),
            })

            print(f"  {m1:>5.1f}  {label}")

        except DataFetchError as e:
            print(f"  DATA ERROR: {e}")
            errors.append((ticker, str(e)))
        except Exception as e:
            print(f"  ERROR: {e}")
            errors.append((ticker, str(e)))

        time.sleep(delay)

    # Save CSV
    csv_path = os.path.join(base, output_csv)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(results)

    # Summary
    print("\n" + "=" * 62)
    print("  SCREENING COMPLETE")
    print(f"  Processed: {len(results)}   Skipped: {len(skipped)}   Errors: {len(errors)}")
    print(f"  Results saved to: {output_csv}")
    print("=" * 62)

    _print_best_buys(results)

    if errors:
        print(f"\n  ERRORS ({len(errors)}):")
        for t, e in errors[:10]:
            print(f"  {t}: {e}")

    print()
    return results


def top_from_csv(csv_path):
    """Re-read last results.csv and print best buys without re-screening."""
    if not os.path.exists(csv_path):
        print(f"  File not found: {csv_path}")
        return

    results = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            results.append({
                "ticker":             row["ticker"],
                "name":               row.get("name", row["ticker"]),
                "score":              float(row["score"]) if row["score"] else 0,
                "classification":     row["classification"],
                "dcf_upside_pct":     float(row["dcf_upside_pct"]) if row.get("dcf_upside_pct") not in (None, "", "N/A", "None") else None,
                "current_price":      float(row["current_price"]) if row.get("current_price") not in (None, "", "N/A", "None") else None,
                "dcf_intrinsic_value":float(row["dcf_intrinsic_value"]) if row.get("dcf_intrinsic_value") not in (None, "", "N/A", "None") else None,
                "shares_outstanding": float(row["shares_outstanding"]) if row.get("shares_outstanding") not in (None, "", "N/A", "None") else None,
            })

    print(f"\n  Loaded {len(results)} results from {csv_path}")
    _print_best_buys(results)
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--top":
        base = os.path.dirname(__file__)
        top_from_csv(os.path.join(base, "results.csv"))
    else:
        ticker_file = sys.argv[1] if len(sys.argv) > 1 else "sp500.txt"
        screen(ticker_file)
