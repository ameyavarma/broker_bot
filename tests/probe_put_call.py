"""Throwaway diagnostic: LABU put vs call, same expiry/strike, side by side.

Puts delivered delayed bid/ask (errors 10090/10091 then data); calls got a
hard error 354 and nothing. Same account, minutes apart. This requests one of
each simultaneously and logs every error + the tick fields, to tell whether
the difference is really put-vs-call or just delayed-farm flakiness.

    python tests/probe_put_call.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from ib_async import IB, Option

import config

EXPIRY, STRIKE = "20260717", 300.0
WAIT = 25.0


def _fmt(v):
    return "-" if v is None or v != v else f"{float(v):.2f}"


def main():
    print(f"local time now: {datetime.now()}")
    ib = IB()
    ib.connect(config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID, timeout=10)
    errors = []
    ib.errorEvent += lambda reqId, code, msg, *a: errors.append((reqId, code, msg))
    try:
        ib.reqMarketDataType(4)
        opts = [Option("LABU", EXPIRY, STRIKE, r, "SMART", tradingClass="LABU")
                for r in ("P", "C")]
        ib.qualifyContracts(*opts)
        tickers = {o.right: ib.reqMktData(o, "", False, False) for o in opts}
        ib.sleep(WAIT)
        for right, t in tickers.items():
            g = t.modelGreeks
            print(f"{right}: mdType={t.marketDataType} bid={_fmt(t.bid)} "
                  f"ask={_fmt(t.ask)} last={_fmt(t.last)} close={_fmt(t.close)} "
                  f"iv={_fmt(g.impliedVol) if g else '-'} "
                  f"delta={_fmt(g.delta) if g else '-'}")
        print("\nerrors received:")
        for reqId, code, msg in errors:
            if not (2100 <= code < 2200):  # skip farm-status noise
                print(f"  [{code}] reqId {reqId}: {msg[:100]}")
        for o in opts:
            ib.cancelMktData(o)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
