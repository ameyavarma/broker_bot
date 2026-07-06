"""Diagnostic: download IBKR's borrow file and confirm reachability + units.

Checks three things before we wire Fee rate into the export:
  1. Can we reach the FTP at all (corporate firewalls may block port 21)?
  2. How many symbols come back?
  3. AAPL's raw FEERATE -- compare it to the 0.0025 TWS shows, to nail the units
     (the file may store a percent like 0.25 while TWS displays the 0.0025
     fraction, i.e. divide-by-100).

    python tests/probe_borrow.py   (run from the project root)
"""
import sys
from pathlib import Path

# Internal modules live in ../core; put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import borrow

AAPL_CONID = 265598  # TWS shows Fee rate 0.0025 for this


def main():
    print("Downloading IBKR securities-lending file (usa.txt)...")
    try:
        data = borrow.fetch_fee_rates()
    except Exception as e:
        print(f"  FAILED to fetch: {type(e).__name__}: {e}")
        print("  -> Could be a blocked FTP port, wrong host/credentials, or no network.")
        raise SystemExit(1)

    print(f"  parsed {len(data)} symbols\n")

    aapl = data.get(AAPL_CONID)
    print(f"AAPL (conId {AAPL_CONID}):")
    if aapl:
        print(f"  raw FEERATE    = {aapl['fee']}")
        print(f"  raw REBATERATE = {aapl['rebate']}")
        print(f"  available      = {aapl['available']}")
        print("  -> TWS shows Fee rate 0.0025. If raw FEERATE is 0.25, units are")
        print("     PERCENT (we divide by 100); if it's already 0.0025, use as-is.")
    else:
        print("  NOT found by conId -- matching may need symbol fallback.")
        # show any AAPL-symbol rows as a fallback hint
        for cid, rec in data.items():
            if rec["symbol"] == "AAPL":
                print(f"    found by symbol: conId={cid} fee={rec['fee']}")

    print("\nFirst 5 records (format sanity check):")
    for cid, rec in list(data.items())[:5]:
        print(f"  {rec['symbol']:8s} conId={cid:>10} fee={rec['fee']} "
              f"rebate={rec['rebate']} avail={rec['available']}")


if __name__ == "__main__":
    main()
