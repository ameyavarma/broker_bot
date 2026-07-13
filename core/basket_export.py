"""Write an OrderPlan as a TWS BasketTrader import file.

Mirrors the boss's hand-made basket CSVs (BuyDFF.csv): same header names and
order, plus the option-contract columns the basket format defines
(LastTradingDayOrContractMonth / Strike / Right) and GoodTilDate when an
order lifetime is set. TWS matches basket columns by header name, so extra
columns are fine and blank cells mean "not applicable".

Files land in OUTPUT_BASKETS_DIR/<YYYY-MM-DD>/<strategy>_<HHMMSS>.csv --
one folder per day, one file per generated basket.
"""
import csv
from datetime import datetime, timedelta

import config

_COLUMNS = [
    "Action", "Quantity", "Symbol", "SecType", "Exchange", "Currency",
    "TimeInForce", "OrderType", "LmtPrice", "Account", "OutsideRth",
    # In his stock baskets this holds "SMART PreferRebate" -- a stock routing
    # preference that doesn't apply to options, so it stays blank here.
    "RoutingStrategyAttribute",
    "LastTradingDayOrContractMonth", "Strike", "Right",
]


def write_basket_csv(plan, account, expire_minutes=None):
    """One SELL row per selected leg, at that leg's ask. Returns the path."""
    now = datetime.now()
    day_dir = config.OUTPUT_BASKETS_DIR / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{plan.strategy}_{now.strftime('%H%M%S')}.csv"

    columns = list(_COLUMNS)
    if expire_minutes:
        # Basket GoodTilDate format is "YYYYMMDD hh:mm:ss" (local time) --
        # note: stamped NOW, so the clock runs from file creation, not load.
        good_til = (now + timedelta(minutes=expire_minutes)
                    ).strftime("%Y%m%d %H:%M:%S")
        columns.append("GoodTilDate")

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(columns)
        for leg in plan.selected:
            row = [
                "SELL", plan.qty_per_leg, plan.ticker, "OPT", "SMART", "USD",
                "GTD" if expire_minutes else "DAY", "LMT", f"{leg.ask:.6f}",
                account, "FALSE", "",
                leg.expiry, f"{leg.strike:g}", plan.right,
            ]
            if expire_minutes:
                row.append(good_til)
            w.writerow(row)
    return path
