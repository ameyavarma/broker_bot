"""Boss option functions: candidate selection + order sizing (NO transmission).

Implements the two functions from OptionFunctions.txt -- both share the same
pipeline and differ only in which strikes they pick per expiry:

  SellNearestOption      -- the single strike closest to the underlying's
                            last price (ATM), per expiry.
  SellOutOfMoneyOption   -- the NUM_OUT_OF_MONEY out-of-the-money strikes
                            closest to the underlying's last price, per
                            expiry (calls: strike > spot; puts: strike < spot).

Common pipeline:

    Search all maturity dates for the next three months.
    For each date: pick strikes (per the function above).
    Keep options whose implied volatility is above IMPLIED_VOLATILITY_THRESHOLD.
    DELTA_SUM           = sum of the kept options' deltas
    NUM_ORDER_CONTRACTS = NUM_CONTRACTS / DELTA_SUM     (rounded to nearest)
    Sell NUM_ORDER_CONTRACTS of EACH kept option at the ask price.

"Of EACH" (not split): selling qty x every kept leg makes the total delta
sold ~= NUM_CONTRACTS, which is what makes his 129 / 3.6 -> 36 example math
meaningful (NUM_CONTRACTS is a delta target, spread across the legs).

Functions here RETURN an OrderPlan describing exactly what would be sold and
why every candidate was kept or skipped. Transmitting the plan is
order_exec.py's job; the entry script (options_order.py) shows the plan and
asks for confirmation in between.
"""
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd
from ib_async import Option, Stock

import fetchers

MONTHS_AHEAD = 3          # "the next three months"
SELECTED = "SELECTED"     # Leg.status for legs that made the cut
RETRY_ROUNDS = 2          # extra quote requests for legs with missing data


def _pos(v):
    """A real positive price/value, else None (filters NaN and IBKR's -1)."""
    return v if v is not None and v == v and v > 0 else None


@dataclass
class Leg:
    """One candidate option: the contract plus everything we learned."""
    expiry: str                    # YYYYMMDD
    contract: object = None       # qualified ib_async Option (once found)
    strike: float = None
    bid: float = None
    ask: float = None
    iv: float = None               # modelGreeks.impliedVol (fraction, 0.62 = 62%)
    delta: float = None
    status: str = ""               # SELECTED, or human-readable skip reason
    data_gap: bool = False         # skipped for MISSING DATA, not by the IV
                                   # filter -- the preview warns loudly on these


@dataclass
class OrderPlan:
    """Everything the preview needs to show and the executor needs to send."""
    strategy: str                  # "SellNearestOption" / "SellOutOfMoneyOption"
    ticker: str
    right: str                     # "P" or "C"
    num_contracts: int
    iv_threshold: float            # fraction
    spot: float
    spot_source: str               # which field supplied the spot ("last"/"close")
    cutoff: date                   # latest expiry considered
    num_otm: int = None            # SellOutOfMoneyOption's NUM_OUT_OF_MONEY
    legs: list = field(default_factory=list)   # every candidate, kept or not
    delta_sum: float = 0.0         # raw sum (negative for puts)
    qty_per_leg: int = 0           # NUM_ORDER_CONTRACTS

    @property
    def selected(self):
        return [leg for leg in self.legs if leg.status == SELECTED]


def _fetch_spot(ib, stock):
    """The underlying's last price (falling back to close), plus which it was.

    The stock is re-routed to its listing exchange first: on accounts without
    API market-data subscriptions, direct-routed requests fall back to the
    delayed feed while SMART-routed ones are refused (see fetchers.fetch_quotes).
    """
    stock.exchange = stock.primaryExchange or "SMART"
    t = fetchers.fetch_quotes(ib, [stock])[stock.conId]
    last, close = _pos(t.last), _pos(t.close)
    if last is not None:
        return last, "last"
    if close is not None:
        return close, "close"
    raise RuntimeError(f"no price at all for {stock.symbol} "
                       "(no last, no close) -- cannot pick strikes")


# --- the two public functions ---------------------------------------------------

def sell_nearest_option(ib, ticker, right, num_contracts, iv_threshold,
                        progress=lambda msg: None, spinner=nullcontext):
    """SellNearestOption: one ATM strike (closest to spot) per expiry."""

    def pick(cds, spot):
        best = min(cds, key=lambda cd: abs(cd.contract.strike - spot))
        return [best.contract]

    return _build_plan(ib, ticker, right, num_contracts, iv_threshold,
                       strategy="SellNearestOption", pick=pick,
                       progress=progress, spinner=spinner)


def sell_out_of_money_option(ib, ticker, right, num_contracts, iv_threshold,
                             num_otm, progress=lambda msg: None,
                             spinner=nullcontext):
    """SellOutOfMoneyOption: the `num_otm` strictly out-of-the-money strikes
    closest to spot, per expiry (calls above spot, puts below)."""

    def pick(cds, spot):
        if right == "C":
            otm = [cd for cd in cds if cd.contract.strike > spot]
        else:
            otm = [cd for cd in cds if cd.contract.strike < spot]
        otm.sort(key=lambda cd: abs(cd.contract.strike - spot))
        return [cd.contract for cd in otm[:num_otm]]

    plan = _build_plan(ib, ticker, right, num_contracts, iv_threshold,
                       strategy="SellOutOfMoneyOption", pick=pick,
                       progress=progress, spinner=spinner)
    plan.num_otm = num_otm
    return plan


# --- shared pipeline -------------------------------------------------------------

def _build_plan(ib, ticker, right, num_contracts, iv_threshold,
                strategy, pick, progress, spinner):
    """Build an OrderPlan. Raises on unusable inputs (unknown ticker, no
    option chain); per-expiry problems become skipped legs with a reason
    instead. `pick(contract_details, spot)` chooses each expiry's contracts;
    `progress` gets one-line status updates; `spinner` is a context-manager
    factory (e.g. console.Spinner) shown around the slow API phases.
    """
    # -- underlying + spot ----------------------------------------------------
    stock = Stock(ticker, "SMART", "USD")
    if not ib.qualifyContracts(stock):
        raise RuntimeError(f"IBKR does not recognize stock ticker {ticker!r}")
    progress(f"underlying {ticker}: conId {stock.conId} "
             f"({stock.primaryExchange})")
    with spinner("fetching underlying price"):
        spot, spot_source = _fetch_spot(ib, stock)
    progress(f"spot price: {spot} (from '{spot_source}')")

    # -- expiries within the window -------------------------------------------
    chains = ib.reqSecDefOptParams(stock.symbol, "", "STK", stock.conId)
    chain = next((c for c in chains
                  if c.exchange == "SMART" and c.tradingClass == stock.symbol),
                 next((c for c in chains if c.exchange == "SMART"), None))
    if chain is None:
        raise RuntimeError(f"no SMART option chain found for {ticker}")
    today = date.today()
    cutoff = (pd.Timestamp(today) + pd.DateOffset(months=MONTHS_AHEAD)).date()
    expiries = sorted(e for e in chain.expirations
                      if today < datetime.strptime(e, "%Y%m%d").date() <= cutoff)
    progress(f"{len(expiries)} expiries within {MONTHS_AHEAD} months "
             f"(through {cutoff})")
    if not expiries:
        raise RuntimeError(f"{ticker} has no option expiries before {cutoff}")

    plan = OrderPlan(strategy=strategy, ticker=ticker, right=right,
                     num_contracts=num_contracts, iv_threshold=iv_threshold,
                     spot=spot, spot_source=spot_source, cutoff=cutoff)

    # -- picked contracts per expiry -------------------------------------------
    # reqContractDetails per expiry returns that expiry's REAL strikes (the
    # chain's .strikes pools all expiries, so it can name strikes an individual
    # expiry doesn't have).
    with spinner(f"looking up strikes for {len(expiries)} expiries"):
        for expiry in expiries:
            cds = ib.reqContractDetails(
                Option(stock.symbol, expiry, 0.0, right, "SMART",
                       tradingClass=chain.tradingClass))
            if not cds:
                plan.legs.append(Leg(expiry=expiry,
                                     status="no contracts listed for this expiry"))
                continue
            picked = pick(cds, spot)
            if not picked:
                plan.legs.append(Leg(expiry=expiry,
                                     status="no out-of-the-money strikes "
                                            "for this expiry"))
                continue
            for c in picked:
                plan.legs.append(Leg(expiry=expiry, contract=c, strike=c.strike))
    with_contract = [leg for leg in plan.legs if leg.contract is not None]
    progress(f"{len(with_contract)} candidate contracts "
             f"across {len(expiries)} expiries")

    # -- quotes + greeks, with targeted retries ---------------------------------
    # Individual contracts sometimes miss their greeks within the wait window
    # (the model-greeks message is its own delivery and can be slow/dropped,
    # especially on the delayed feed). A FRESH subscription restarts delivery
    # and usually succeeds at once, so incomplete legs get re-requested up to
    # RETRY_ROUNDS times before we accept a gap.
    def _apply(leg, t):
        g = t.modelGreeks if t is not None else None
        leg.bid = _pos(t.bid) if t else None
        leg.ask = _pos(t.ask) if t else None
        if g is not None:
            leg.iv = _pos(g.impliedVol)
            leg.delta = g.delta if g.delta == g.delta else None  # NaN -> None

    def _complete(leg):
        return leg.iv is not None and leg.delta is not None and leg.ask is not None

    with spinner("waiting for quotes + greeks"):
        quotes = fetchers.fetch_quotes(ib, [leg.contract for leg in with_contract])
    for leg in with_contract:
        _apply(leg, quotes.get(leg.contract.conId))

    if not any(_complete(leg) for leg in with_contract):
        # Nothing came back complete: the options market is closed / feed has
        # no quotes at all. Retrying won't conjure data -- skip straight to
        # the (all-skip) report rather than stalling for extra rounds.
        progress("no complete quotes at all -- options market likely closed")
    else:
        for attempt in range(1, RETRY_ROUNDS + 1):
            missing = [leg for leg in with_contract if not _complete(leg)]
            if not missing:
                break
            progress(f"{len(missing)} legs missing data -- retry {attempt} "
                     f"of {RETRY_ROUNDS}")
            with spinner(f"re-requesting {len(missing)} incomplete legs"):
                quotes = fetchers.fetch_quotes(
                    ib, [leg.contract for leg in missing])
            for leg in missing:
                _apply(leg, quotes.get(leg.contract.conId))

    # Keep/skip, with the reason the preview will show. Skips for MISSING DATA
    # (vs. failing the IV filter) also set data_gap, which the preview turns
    # into a warning at the transmission prompt.
    for leg in with_contract:
        if leg.iv is None:
            leg.status = "no implied volatility arrived (even after retries)"
            leg.data_gap = True
        elif leg.iv <= iv_threshold:
            leg.status = f"IV {leg.iv:.1%} not above threshold"
        elif leg.delta is None:
            leg.status = "IV qualifies but no delta arrived"
            leg.data_gap = True
        elif leg.ask is None:
            leg.status = "IV qualifies but no ask price to sell at"
            leg.data_gap = True
        else:
            leg.status = SELECTED

    # -- sizing -----------------------------------------------------------------
    # Puts have negative deltas, so the raw sum is negative; sizing uses |sum|.
    plan.delta_sum = sum(leg.delta for leg in plan.selected)
    if plan.selected and abs(plan.delta_sum) > 1e-9:
        plan.qty_per_leg = int(num_contracts / abs(plan.delta_sum) + 0.5)
    progress(f"{len(plan.selected)} legs selected, "
             f"DELTA_SUM={plan.delta_sum:.3f}, qty/leg={plan.qty_per_leg}")
    return plan
