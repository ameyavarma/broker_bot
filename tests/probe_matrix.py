"""Throwaway diagnostic: matrix-test market data delivery.

For each position, request market data under every combination of
  market data type : 3 (delayed) vs 4 (delayed-frozen)
  routing          : direct listing exchange vs SMART
and report (a) which IBKR errors fire and (b) which ticker fields populate.

Run from the project root with TWS up:
    python tests/probe_matrix.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from ib_async import IB, Contract

import config

CLIENT_ID = config.IB_CLIENT_ID  # reuse the trusted clientId (main scripts are not running)
WAIT = 8.0

FIELDS = ["bid", "ask", "last", "close",
          "delayedBid", "delayedAsk", "delayedLast", "delayedClose"]


def _fmt(v):
    if v is None or v != v:  # None or NaN
        return "-"
    return f"{float(v):.2f}"


def main():
    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=CLIENT_ID, timeout=10)

    errors = []  # (reqId, code, message) tuples, appended as they arrive
    ib.errorEvent += lambda reqId, code, msg, *a: errors.append((reqId, code, msg))

    try:
        positions = ib.positions(config.IB_ACCOUNT)
        base = [(p.contract.conId, p.contract.symbol,
                 p.contract.exchange or p.contract.primaryExchange or "NASDAQ")
                for p in positions]
        print(f"{len(base)} positions: "
              + ", ".join(f"{sym}(exch={exch!r})" for _, sym, exch in base))

        combos = [(4, "direct"), (4, "SMART"), (3, "direct"), (3, "SMART")]
        for md_type, routing in combos:
            print(f"\n=== type {md_type}, routing {routing} " + "=" * 30)
            ib.reqMarketDataType(md_type)
            ib.sleep(0.5)

            tickers = []
            for con_id, sym, exch in base:
                c = Contract(conId=con_id, exchange=exch if routing == "direct" else "SMART")
                errors.clear()
                t = ib.reqMktData(c, "", False, False)
                tickers.append((sym, c, t))
            ib.sleep(WAIT)

            for sym, c, t in tickers:
                vals = "  ".join(f"{f}={_fmt(getattr(t, f, None))}" for f in FIELDS)
                print(f"  {sym:6s} mdType={t.marketDataType}  {vals}")
            for reqId, code, msg in errors:
                if code in (2104, 2106, 2158, 2119):  # farm-status noise
                    continue
                print(f"  [error {code}] reqId {reqId}: {msg[:110]}")
            for _, c, _t in tickers:
                ib.cancelMktData(c)
            ib.sleep(0.5)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
