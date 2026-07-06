"""Throwaway diagnostic: why does the XSP option chain look empty?

Checks, in order:
  1. the XSP index itself (delayed quote -> do we get an underlying price?)
  2. an ATM XSP option's ContractDetails.tradingHours (is the overnight
     Global Trading Hours session open right now?)
  3. delayed market data + model greeks for that option

Run from the project root with TWS up:
    python tests/probe_xsp.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from ib_async import IB, Index, Option

import config

WAIT = 12.0


def _fmt(v):
    if v is None or v != v:
        return "-"
    return f"{float(v):.2f}"


def main():
    print(f"local time now: {datetime.now()}")
    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID, timeout=10)
    try:
        ib.reqMarketDataType(4)  # delayed-frozen, same as the portfolio script

        # 1. the underlying index
        idx = Index("XSP", "CBOE", "USD")
        ib.qualifyContracts(idx)
        t = ib.reqMktData(idx, "", False, False)
        ib.sleep(6)
        # ib_async normalizes delayed ticks into the regular fields
        print(f"\nXSP index: last={_fmt(t.last)} close={_fmt(t.close)}")
        ib.cancelMktData(idx)
        # IBKR sends -1 as a "no data" sentinel; only a positive price is real
        spot = next((v for v in (t.last, t.close)
                     if v is not None and v == v and v > 0), None)
        if spot is None:
            print("  no index value at all -- stopping here")
            return

        # 2. option chain -> nearest expiry, ATM strike
        chains = ib.reqSecDefOptParams("XSP", "", "IND", idx.conId)
        chain = next(c for c in chains if c.exchange in ("CBOE", "SMART"))
        expiry = sorted(chain.expirations)[1]  # skip 0DTE, take the next one
        strike = min(chain.strikes, key=lambda s: abs(s - spot))
        print(f"chain: exchange={chain.exchange} tradingClass={chain.tradingClass} "
              f"-> trying expiry {expiry}, strike {strike}")

        for right in ("C", "P"):
            opt = Option("XSP", expiry, strike, right, "SMART",
                         tradingClass=chain.tradingClass)
            ib.qualifyContracts(opt)

            # trading-hours schedule straight from IBKR
            cd = ib.reqContractDetails(opt)[0]
            print(f"\n{right}: conId={opt.conId} exchange={opt.exchange}")
            print(f"  timeZone={cd.timeZoneId}")
            print(f"  tradingHours (today ff.): {cd.tradingHours.split(';')[0]} ; "
                  f"{cd.tradingHours.split(';')[1] if ';' in cd.tradingHours else ''}")

            # 3. delayed quote + greeks
            t = ib.reqMktData(opt, "", False, False)
            waited = 0.0
            while waited < WAIT:
                ib.sleep(0.5)
                waited += 0.5
                if t.modelGreeks is not None:
                    break
            g = t.modelGreeks
            print(f"  bid={_fmt(t.bid)} ask={_fmt(t.ask)} last={_fmt(t.last)} "
                  f"close={_fmt(t.close)}")
            print(f"  greeks: " + (f"delta={_fmt(g.delta)} undPrice={_fmt(g.undPrice)} "
                                   f"iv={_fmt(g.impliedVol)}" if g else "none"))
            ib.cancelMktData(opt)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
