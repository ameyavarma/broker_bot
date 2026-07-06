"""Throwaway diagnostic: validate the UTC goodTillDate format against TWS.

Places one deliberately unfillable order (BUY 1 far-OTM LABU call, limit 0.01)
with tif=GTD in the 'yyyymmdd-hh:mm:ss' UTC format order_exec.good_till()
produces, watches for acceptance vs. warnings/errors (esp. 2174), then cancels.

    python tests/probe_gtd.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from ib_async import LimitOrder, Option

import config
import connection
import order_exec


def main():
    with connection.connect() as ib:
        errors = []
        ib.errorEvent += lambda reqId, code, msg, *a: errors.append((code, msg))

        # the farthest-OTM LABU call that actually exists for this expiry
        cds = ib.reqContractDetails(
            Option("LABU", "20260918", 0.0, "C", "SMART", tradingClass="LABU"))
        opt = max(cds, key=lambda cd: cd.contract.strike).contract
        print(f"using strike {opt.strike} (highest listed)")

        gtd = order_exec.good_till(3)
        print(f"goodTillDate sent: {gtd!r}")
        order = LimitOrder("BUY", 1, 0.01, tif="GTD", goodTillDate=gtd)
        trade = ib.placeOrder(opt, order)
        ib.sleep(6)
        print(f"status after 6s: {trade.orderStatus.status}")
        for e in trade.log:
            if e.errorCode:
                print(f"  order log: [{e.errorCode}] {e.message[:90]}")
        tz_warnings = [(c, m) for c, m in errors if c == 2174]
        print(f"2174 timezone warnings: {len(tz_warnings)}")

        ib.cancelOrder(order)
        ib.sleep(2)
        print(f"after cancel: {trade.orderStatus.status}")


if __name__ == "__main__":
    main()
