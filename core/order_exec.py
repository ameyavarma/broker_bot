"""Transmit an OrderPlan as sell-limit orders and report what came back.

Kept deliberately separate from option_strategies.py (which only computes
plans) so nothing can transmit an order without passing through the entry
script's preview + confirmation step.
"""
from datetime import datetime, timedelta, timezone

from ib_async import LimitOrder


def good_till(minutes):
    """IBKR goodTillDate string for `minutes` from now.

    The dashed 'yyyymmdd-hh:mm:ss' form is interpreted as UTC -- the format
    IBKR asks for (warning 2174: timezone-less timestamps are deprecated).
    """
    when = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return when.strftime("%Y%m%d-%H:%M:%S")


def execute_sell_plan(ib, plan, expire_minutes=None, wait=10.0, staged=False):
    """Place one SELL limit order per selected leg, at that leg's ask price
    (per the spec: "sell ... at the ask price").

    expire_minutes: auto-cancel unfilled orders after this many minutes (GTD);
    None = DAY (unfilled orders die at the session close).

    staged: place with transmit=False -- TWS holds the orders (pink rows on
    the Orders tab, each with a Transmit button) and NOTHING reaches the
    exchange until the user clicks Transmit per order. Held orders survive
    this script disconnecting but are discarded if TWS restarts. TWS sends
    no status events while an order is held, so the status poll is skipped.

    Returns [(leg, Trade)]. We poll up to `wait` seconds for order statuses
    to settle; orders that don't fill immediately simply stay working at
    IBKR -- PreSubmitted/Submitted is a normal outcome here, not an error.
    """
    # tif is set explicitly: left blank, TWS fills it from its order preset
    # and reports informational message 10349 -- which ib_async misclassifies
    # as a fatal order error and wrongly marks the (live!) order Cancelled.
    if expire_minutes:
        tif = dict(tif="GTD", goodTillDate=good_till(expire_minutes))
    else:
        tif = dict(tif="DAY")
    trades = [(leg, ib.placeOrder(leg.contract,
                                  LimitOrder("SELL", plan.qty_per_leg, leg.ask,
                                             transmit=not staged, **tif)))
              for leg in plan.selected]
    if staged:
        ib.sleep(1)  # let TWS acknowledge receipt before we report/disconnect
        return trades
    waited = 0.0
    while waited < wait:
        ib.sleep(0.5)
        waited += 0.5
        if all(t.orderStatus.status in ("Filled", "Cancelled", "Inactive")
               for _, t in trades):
            break  # everything reached a final state early
    return trades


def trade_report_row(trade):
    """One human-readable status line for a placed order."""
    st = trade.orderStatus
    line = f"{st.status}: filled {int(st.filled)}/{int(trade.order.totalQuantity)}"
    if st.filled:
        line += f" @ avg {st.avgFillPrice}"
    # Surface IBKR's own words on rejections/warnings (margin, permissions...)
    errors = [e.message for e in trade.log if e.errorCode]
    if errors:
        line += f" -- IBKR says: {errors[-1]}"
    return line
