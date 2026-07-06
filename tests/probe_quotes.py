"""Throwaway diagnostic: prove what market data we actually get back.

Pulls current positions, requests market data for each, and prints the fields
we need for the boss-format file -- especially Delta and Underlying Price,
which come from option model greeks and may be absent on delayed/paper data.

Run with positions open in the paper account:
    python probe_quotes.py
Then try MARKET_DATA_TYPE = 1 (live) vs 3 (delayed) to compare.

    python tests/probe_quotes.py   (run from the project root)
"""
import sys
from pathlib import Path

# Internal modules live in ../core; put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import connection
import fetchers
import config

MARKET_DATA_TYPE = 4  # 1=live, 3=delayed, 4=delayed-frozen (last values, market closed)


def _g(ticker, attr):
    """modelGreeks.<attr> if present, else None."""
    greeks = getattr(ticker, "modelGreeks", None)
    return getattr(greeks, attr, None) if greeks else None


def _px(ticker, live_attr, delayed_attr):
    """Prefer the live field; fall back to its delayed counterpart.

    Delayed data (paper accounts) lands in delayedBid/delayedAsk/delayedLast
    while bid/ask/last stay NaN, so we check both.
    """
    v = getattr(ticker, live_attr, None)
    if v is None or v != v:  # None or NaN
        v = getattr(ticker, delayed_attr, None)
    return v


def main():
    with connection.connect() as ib:
        ib.reqMarketDataType(MARKET_DATA_TYPE)
        positions = ib.positions(config.IB_ACCOUNT)
        contracts = [p.contract for p in positions]
        print(f"{len(contracts)} positions; requesting market data "
              f"(type {MARKET_DATA_TYPE})...\n")
        print("contract exchanges as returned by positions():")
        for c in contracts:
            print(f"  {(c.localSymbol or c.symbol):28s} {c.secType:5s} "
                  f"exchange={c.exchange!r}  primaryExchange={c.primaryExchange!r}")
        print()

        quotes = fetchers.fetch_quotes(ib, contracts, wait=8.0)

        hdr = (f"{'instrument':28s} {'secType':7s} {'bid':>9} {'ask':>9} "
               f"{'last':>9} {'delta':>8} {'undPx':>9} {'iv':>7}")
        print(hdr)
        print("-" * len(hdr))
        for p in positions:
            c = p.contract
            t = quotes.get(c.conId)
            if t is None:
                print(f"{(c.localSymbol or c.symbol):28s} {c.secType:7s}  (no ticker)")
                continue
            name = c.localSymbol or c.symbol
            bid = _px(t, "bid", "delayedBid")
            ask = _px(t, "ask", "delayedAsk")
            last = _px(t, "last", "delayedLast")
            delta = _g(t, "delta")
            und = _g(t, "undPrice")
            iv = _g(t, "impliedVol")
            print(f"{name:28s} {c.secType:7s} "
                  f"{_fmt(bid):>9} {_fmt(ask):>9} {_fmt(last):>9} "
                  f"{_fmt(delta):>8} {_fmt(und):>9} {_fmt(iv):>7}")


def _fmt(v):
    """Compact display: blank for None/NaN, else trimmed number."""
    if v is None:
        return "-"
    try:
        if v != v:  # NaN
            return "nan"
        return f"{float(v):.4g}"
    except (TypeError, ValueError):
        return str(v)


if __name__ == "__main__":
    main()
