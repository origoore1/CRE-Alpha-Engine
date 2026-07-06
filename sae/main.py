"""
main.py — SAE Phase 1
Usage:  python main.py AAPL
        python main.py AAPL MSFT NVDA
"""

import sys
from data_fetcher import fetch, DataFetchError
from ratio_calculator import calculate
from scorer import score
from output import print_scorecard


def analyse(ticker: str) -> None:
    print(f"\nFetching data for {ticker.upper()}...")
    try:
        data   = fetch(ticker)
        ratios = calculate(data)
        scores = score(ratios, data)
        print_scorecard(data, ratios, scores)
    except DataFetchError as e:
        print(f"  DATA ERROR: {e}")
    except Exception as e:
        print(f"  UNEXPECTED ERROR for {ticker}: {e}")
        raise


def main():
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL"]
    for t in tickers:
        analyse(t)


if __name__ == "__main__":
    main()
