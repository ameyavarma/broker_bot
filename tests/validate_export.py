"""Validation: pull every column from the manual TWS export via the API, so we
can lay our API output next to a fresh manual export and confirm they agree.

Mirrors the columns/order of the hand-exported test.csv:
    Financial Instrument, Position, Avg Price, Market Value, Daily P&L,
    Bid Size, Bid, Ask, Ask Size, Last, Change, Change %, In The Money

To validate the LIVE columns (everything except Instrument/Position/Avg Price),
run this AND do a fresh manual export at the same moment -- quotes move, so a
side-by-side only ties out if both are captured together.

    python tests/validate_export.py   (run from the project root)
"""
import math
import sys
from pathlib import Path

# Internal modules live in ../core; put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

import connection
import config
import fetchers
from export_portfolio import _instrument_name, _price, _greek, _clean, _OPTION_TYPES

MARKET_DATA_TYPE = 4  # delayed-frozen for paper; use 1 on a live-subscribed account


def _change(ticker):
    """(last - prior close) in dollars, and as a fraction -- his Change / Change %."""
    last = _price(ticker, "last", "delayedLast")
    close = _clean(getattr(ticker, "close", None))
    if last is None or close in (None, 0):
        return None, None
    chg = last - close
    return chg, chg / close


def _in_the_money(contract, ticker):
    """His 'In The Money': signed intrinsic for options (undPrice - strike for a
    call, strike - undPrice for a put); blank for stocks."""
    if contract.secType not in _OPTION_TYPES:
        return None
    und = _greek(ticker, "undPrice")
    if und is None:
        return None
    strike = float(contract.strike)
    return und - strike if contract.right in ("C", "CALL") else strike - und


def _fmt(v, nd=2):
    if v is None:
        return "-"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def main():
    with connection.connect() as ib:
        ib.reqMarketDataType(MARKET_DATA_TYPE)
        positions = ib.positions(config.IB_ACCOUNT)
        contracts = [p.contract for p in positions]
        print(f"{len(positions)} positions; pulling all export columns "
              f"(market data type {MARKET_DATA_TYPE})...\n")

        # Subscribe to per-position P&L first; one PnLSingle carries BOTH the
        # daily P&L and the position's market value (.value), so we don't need
        # reqAccountUpdates/portfolio() (which can block). fetch_quotes' built-in
        # wait, plus a short extra sleep, lets the P&L values populate.
        pnl = {p.contract.conId: ib.reqPnLSingle(p.account, "", p.contract.conId)
               for p in positions}
        # Generic ticks for the newly-added columns:
        #   236 = shortable shares, 106 = option implied vol, 165 = misc stats
        #   (avg volume etc.), 104 = historical volatility.
        quotes = fetchers.fetch_quotes(ib, contracts, generic_ticks="236,106,165,104")
        ib.sleep(2)  # give P&L singles a moment to fill in

        cols = ["instrument", "pos", "avgPx", "mktVal", "dailyPnL",
                "bidSz", "bid", "ask", "askSz", "last", "chg", "chg%", "ITM"]
        widths = [24, 6, 9, 11, 9, 6, 8, 8, 6, 8, 8, 8, 7]
        print("  ".join(c.rjust(w) for c, w in zip(cols, widths)))
        print("-" * (sum(widths) + 2 * len(widths)))

        for p in positions:
            c = p.contract
            cid = c.conId
            t = quotes.get(cid)
            single = pnl.get(cid)
            mult = float(c.multiplier) if c.multiplier else 1.0
            chg, chg_pct = _change(t)
            vals = [
                _instrument_name(c),
                int(p.position) if float(p.position).is_integer() else p.position,
                _fmt(p.avgCost / mult, 3),
                _fmt(getattr(single, "value", None), 0),
                _fmt(getattr(single, "dailyPnL", None), 0),
                _fmt(_price(t, "bidSize", "delayedBidSize"), 0),
                _fmt(_price(t, "bid", "delayedBid")),
                _fmt(_price(t, "ask", "delayedAsk")),
                _fmt(_price(t, "askSize", "delayedAskSize"), 0),
                _fmt(_price(t, "last", "delayedLast")),
                _fmt(chg),
                _fmt(chg_pct, 4),
                _fmt(_in_the_money(c, t)),
            ]
            print("  ".join(str(v).rjust(w) for v, w in zip(vals, widths)))

        # --- newly-added columns: Shortable Shares, IV, (Fee rate?) -----------
        print("\nNewly-added columns vs TEST3 "
              "(shortableShares=191329370, feeRate=0.0025, IV stock=23.1% opt=25%):")
        for p in positions:
            c = p.contract
            t = quotes.get(c.conId)
            iv_opt = _greek(t, "impliedVol")          # option IV (from greeks)
            iv_stk = _clean(getattr(t, "impliedVolatility", None))  # stock IV (tick 106)
            print(f"  {_instrument_name(c):24s} "
                  f"shortableShares={getattr(t, 'shortableShares', None)}  "
                  f"impliedVolatility(stk)={iv_stk}  "
                  f"modelGreeks.impliedVol(opt)={iv_opt}  "
                  f"histVolatility={_clean(getattr(t, 'histVolatility', None))}")

        # --- discovery dump: find which field holds Fee rate (~0.0025) --------
        stock_t = next((quotes.get(p.contract.conId) for p in positions
                        if p.contract.secType == "STK"), None)
        if stock_t is not None:
            print("\nDiscovery dump -- all non-empty numeric fields on the stock "
                  "ticker (look for 0.0025 = fee rate, 0.231 = IV):")
            for attr in sorted(vars(stock_t)):
                val = getattr(stock_t, attr)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    if val == 0 or (isinstance(val, float) and math.isnan(val)):
                        continue
                    print(f"    {attr} = {val}")

        for p in positions:
            ib.cancelPnLSingle(p.account, "", p.contract.conId)


if __name__ == "__main__":
    main()
