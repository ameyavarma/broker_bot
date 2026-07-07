"""Read functions: pull data from IBKR and return tidy pandas DataFrames.

Each function takes a connected `ib` (from connection.connect()) and returns a
DataFrame. No file I/O and no formatting decisions live here -- that is export's job.
"""
import pandas as pd
from ib_async import Contract

import config


def _format_combo_legs(contract):
    """Serialize a combo/spread's legs into a readable string (empty if not a combo)."""
    legs = contract.comboLegs or []
    return "; ".join(f"{leg.action} {leg.ratio}x{leg.conId}" for leg in legs)


def _contract_fields(c):
    """All useful scalar fields from an IBKR contract, flattened for CSV."""
    return {
        "conId": c.conId,
        "symbol": c.symbol,
        "secType": c.secType,
        "localSymbol": c.localSymbol,
        "tradingClass": c.tradingClass,
        "right": c.right,
        "strike": c.strike,
        "expiry": c.lastTradeDateOrContractMonth,
        "multiplier": c.multiplier,
        "exchange": c.exchange,
        "primaryExchange": c.primaryExchange,
        "currency": c.currency,
        "secId": c.secId,
        "secIdType": c.secIdType,
        "comboLegsDescrip": c.comboLegsDescrip,
        "comboLegs": _format_combo_legs(c),
    }


# Stable column order for the flattened contract fields, even when there are no rows.
_CONTRACT_COLUMNS = list(_contract_fields(Contract()).keys())


def fetch_account_summary(ib):
    """Account-level summary values (net liquidation, cash, buying power, ...)."""
    rows = [
        {"account": v.account, "tag": v.tag, "value": v.value, "currency": v.currency}
        for v in ib.accountSummary(config.IB_ACCOUNT)
    ]
    return pd.DataFrame(rows, columns=["account", "tag", "value", "currency"])


def fetch_positions(ib):
    """Current holdings: one row per open position."""
    rows = []
    for p in ib.positions(config.IB_ACCOUNT):
        rows.append({
            "account": p.account,
            **_contract_fields(p.contract),
            "position": p.position,
            "avgCost": p.avgCost,
        })
    columns = ["account"] + _CONTRACT_COLUMNS + ["position", "avgCost"]
    return pd.DataFrame(rows, columns=columns)


def _is_ready(ticker):
    """A ticker is ready once its bid AND ask ticks have ARRIVED -- and, for
    options, the model greeks too.

    "Arrived" is not "has a value": IBKR sends -1 when a side genuinely has no
    quote, while a field that hasn't been sent yet stays NaN. Requiring mere
    arrival means we never hang on quote-less contracts, but we also never
    return early just because a fast field (close, greeks) landed first --
    which used to truncate the wait before bid/ask made it in.
    """
    if ticker.bid != ticker.bid or ticker.ask != ticker.ask:  # NaN = not yet
        return False
    if ticker.contract.secType in ("OPT", "FOP"):
        return ticker.modelGreeks is not None
    return True


def _stream_once(ib, contracts, wait, generic_ticks):
    """Subscribe to each contract, poll until every ticker is ready (or the
    `wait` cap), cancel the streams, and return the captured tickers."""
    tickers = [ib.reqMktData(c, generic_ticks, False, False) for c in contracts]
    waited = 0.0
    while waited < wait:
        ib.sleep(0.25)
        waited += 0.25
        if all(_is_ready(t) for t in tickers):
            break
    for c in contracts:
        ib.cancelMktData(c)
    return tickers


def fetch_quotes(ib, contracts, wait=15.0, generic_ticks="", progress=None):
    """Live or delayed market-data snapshot for each contract, keyed by conId.

    Uses STREAMING market data (reqMktData), not one-shot snapshots: delayed
    data (reqMarketDataType 3/4) only flows over a streaming subscription --
    snapshots require a paid live-data subscription and otherwise raise error
    10089. We start the streams, let ticks (and option greeks) arrive for
    `wait` seconds, capture the Ticker objects, then cancel the streams.

    `generic_ticks` is a comma-separated list of IBKR generic tick IDs for
    extra fields beyond the default quote (e.g. "236" shortable, "106" option
    implied vol, "165" misc stats). Empty string = default quote only.

    Returns {conId: Ticker}. Each Ticker carries quote fields (bid/ask/last and
    their delayed* counterparts, sizes, volume, close, ...) and, for options,
    `modelGreeks` (delta, impliedVol, undPrice). The caller chooses live vs
    delayed beforehand via ib.reqMarketDataType() -- 1=live, 3=delayed,
    4=delayed-frozen.
    """
    # On multi-account logins the same instrument shows up once per account
    # that holds it. ib_async keys market-data subscriptions by contract, so
    # duplicate reqMktData/cancelMktData pairs are redundant -- the repeat
    # cancel is what printed the noisy (harmless) "cancelMktData: No reqId
    # found" lines. Subscribe once per conId.
    seen, unique = set(), []
    for c in contracts:
        if c.conId not in seen:
            seen.add(c.conId)
            unique.append(c)
    contracts = unique
    if not contracts:
        return {}
    # Keep the exchange exactly as positions() returned it, only filling in
    # SMART when blank (a blank exchange makes reqMktData route nowhere).
    # Do NOT rewrite a listing exchange (e.g. NASDAQ) to SMART: on accounts
    # without the API data subscription, direct-routed requests fall back to
    # the delayed feed (error 10167) and deliver, while SMART-routed requests
    # are refused outright (error 10089, no fallback, no data).
    for c in contracts:
        if not c.exchange:
            c.exchange = "SMART"
    # IBKR caps concurrent market-data lines (~100 by default); subscribing to
    # everything at once breaks on big requests (e.g. OTM ladders on underlyings
    # with daily expiries). Work in batches comfortably under the cap.
    # (_stream_once polls rather than a single fixed sleep: on unsubscribed
    # accounts IBKR first bounces the request (10089/10091), THEN falls back
    # to the delayed feed (10167) -- and that fallback can take seconds, so a
    # short fixed wait sometimes captured the tickers while still empty.)
    quotes = {}
    BATCH = 75
    for start in range(0, len(contracts), BATCH):
        batch = contracts[start:start + BATCH]
        quotes.update({t.contract.conId: t
                       for t in _stream_once(ib, batch, wait, generic_ticks)})

    # -- targeted retries for options missing their greeks -----------------------
    # The model-greeks message (delta / IV / undPrice) is its own delivery and
    # can individually lag or drop, especially on the delayed feed. A FRESH
    # subscription restarts delivery and usually succeeds at once, so options
    # still missing greeks get re-requested up to GREEK_RETRIES times. Skipped
    # when NO option got greeks at all (feed simply isn't serving them, e.g.
    # market closed) -- retrying then would only stall.
    GREEK_RETRIES = 2

    def _opt_no_greeks(t):
        return t.contract.secType in ("OPT", "FOP") and t.modelGreeks is None

    got_any_greeks = any(t.contract.secType in ("OPT", "FOP")
                         and t.modelGreeks is not None for t in quotes.values())
    if got_any_greeks:
        for attempt in range(1, GREEK_RETRIES + 1):
            missing = [t.contract for t in quotes.values() if _opt_no_greeks(t)]
            if not missing:
                break
            if progress:
                progress(f"{len(missing)} options missing greeks -- "
                         f"retry {attempt} of {GREEK_RETRIES}")
            for start in range(0, len(missing), BATCH):
                batch = missing[start:start + BATCH]
                quotes.update({t.contract.conId: t
                               for t in _stream_once(ib, batch, wait,
                                                     generic_ticks)})
    return quotes


def fetch_pnl(ib, positions, wait=4.0):
    """Per-position P&L and market value via reqPnLSingle, keyed by conId.

    Each PnLSingle carries .value (market value), .unrealizedPnL, .realizedPnL,
    and .dailyPnL. We subscribe for every position, let the values populate,
    capture the live objects, then cancel the subscriptions.

    (reqPnLSingle is used rather than ib.portfolio() because the latter needs
    reqAccountUpdates, which can block; the per-position subscription is the
    validated path.)
    """
    positions = list(positions)
    if not positions:
        return {}
    subs = {p.contract.conId: ib.reqPnLSingle(p.account, "", p.contract.conId)
            for p in positions}
    ib.sleep(wait)  # let the P&L values fill in
    for p in positions:
        ib.cancelPnLSingle(p.account, "", p.contract.conId)
    return subs


def fetch_trades(ib):
    """Executions (fills) for the current trading day, one row per fill.

    A fill and its commission report arrive on separate IBKR messages, so we
    request executions, briefly run the event loop to let the commission reports
    land, then read ib.fills() -- which has each report attached.
    """
    ib.reqExecutions()
    ib.sleep(2)  # ib.sleep pumps the event loop so commission reports arrive
    rows = []
    for f in ib.fills():
        c, e, cr = f.contract, f.execution, f.commissionReport
        rows.append({
            "time": e.time,
            **_contract_fields(c),
            "side": e.side,
            "shares": e.shares,
            "price": e.price,
            "avgPrice": e.avgPrice,
            "cumQty": e.cumQty,
            "execExchange": e.exchange,
            "permId": e.permId,
            "orderId": e.orderId,
            "clientId": e.clientId,
            "execId": e.execId,
            "acctNumber": e.acctNumber,
            "liquidation": e.liquidation,
            "lastLiquidity": e.lastLiquidity,
            "orderRef": e.orderRef,
            "modelCode": e.modelCode,
            "evRule": e.evRule,
            "evMultiplier": e.evMultiplier,
            "pendingPriceRevision": e.pendingPriceRevision,
            "commission": cr.commission,
            "commissionCurrency": cr.currency,
            "realizedPNL": cr.realizedPNL,
            "yield": cr.yield_,
            "yieldRedemptionDate": cr.yieldRedemptionDate,
        })
    columns = (
        ["time"] + _CONTRACT_COLUMNS
        + ["side", "shares", "price", "avgPrice", "cumQty", "execExchange",
           "permId", "orderId", "clientId", "execId", "acctNumber",
           "liquidation", "lastLiquidity", "orderRef", "modelCode",
           "evRule", "evMultiplier", "pendingPriceRevision",
           "commission", "commissionCurrency", "realizedPNL",
           "yield", "yieldRedemptionDate"]
    )
    return pd.DataFrame(rows, columns=columns)
