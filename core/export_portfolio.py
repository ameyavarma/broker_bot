"""Boss-format view: arrange positions + market data into his PricesDeltas layout.

This is a presentation layer, separate from export_snapshot.py (the raw,
normalized CSVs). It takes the same fetched data and renders the specific file
his TWS export produces -- three sections (long / short / options), his exact
column order -- but WITHOUT the TWS export artifacts: we emit real negative
numbers and plain numerics instead of "'-520" and "1,640".

Scope: all 19 columns of his PricesDeltas layout, in his exact order.

Empty cells: PROVISIONALLY a single signifier -- blank "" -- for every empty
cell (see _empty). We derived a stock->'NoMD' / option->blank rule from his big
file, but a fresh export from another account placed the markers in the OPPOSITE
cells, proving TWS's NoMD-vs-blank is an unstable, subscription-dependent
artifact. Until he confirms whether his backend cares about the distinction, we
use blank everywhere -- the one marker that appears across all instruments and
columns in his file.

Volume is left blank (which TWS volume metric he uses is unconfirmed). Fee rate
is filled for stocks from IBKR's securities-lending file (see borrow.py); if that
file can't be fetched, Fee rate falls back to blank.
"""
from datetime import datetime
from pathlib import Path
import math

import pandas as pd

import config

# _row() builds his full PricesDeltas layout; COLUMNS selects what we OUTPUT.
# His boss needs only columns A-G (through Underlying Price) -- the block where
# TWS's own export is broken; everything from H on he exports reliably himself.
# To output more later, just append names from FULL_COLUMNS below (already computed).
COLUMNS = [
    "Financial Instrument", "Delta", "Bid", "Ask", "Position", "Avg Price",
    "Underlying Price",
]

# The complete 19-column layout, for reference / easy re-expansion:
FULL_COLUMNS = COLUMNS + [
    "Market Value", "Unrealized P&L", "Realized P&L", "Bid Size", "Ask Size",
    "Last", "Change %", "Volume", "Fee rate", "Shortable Shares",
    "Ticker Action", "Opt. Implied Volatility %",
]

NOMD = "NoMD"  # his marker for "no market-data value here" (e.g. a stock's delta)
_OPTION_TYPES = ("OPT", "FOP")
_RIGHT = {"C": "CALL", "P": "PUT", "CALL": "CALL", "PUT": "PUT"}


# --- value extraction --------------------------------------------------------

def _clean(v):
    """Return None for None / NaN / IBKR's unset sentinel, else the value unchanged.

    IBKR encodes "no value" for doubles as DBL_MAX (~1.7977e308), not NaN -- e.g.
    realizedPnL when nothing has been realized. We treat that absurd magnitude as
    missing so it flows through the NoMD/blank policy instead of printing a
    309-digit number.
    """
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v):
            return None
        if abs(v) > 1e300:  # IBKR UNSET_DOUBLE sentinel (DBL_MAX)
            return None
    return v


def _price(ticker, live_attr, delayed_attr):
    """Quote field, preferring the live tick and falling back to its delayed
    counterpart (paper accounts populate delayedBid/Ask/Last, not bid/ask/last).
    """
    if ticker is None:
        return None
    v = _clean(getattr(ticker, live_attr, None))
    if v is None:
        v = _clean(getattr(ticker, delayed_attr, None))
    return v


def _greek(ticker, attr):
    """An option model greek (delta/undPrice/impliedVol), or None if absent."""
    if ticker is None:
        return None
    greeks = getattr(ticker, "modelGreeks", None)
    if not greeks:
        return None
    return _clean(getattr(greeks, attr, None))


# --- formatting --------------------------------------------------------------

def _fmt_strike(strike):
    """310.0 -> '310', 152.5 -> '152.5'."""
    s = float(strike)
    return str(int(s)) if s.is_integer() else f"{s:g}"


def _instrument_name(c):
    """His 'Financial Instrument' string. Stock = symbol; option = e.g.
    'AAPL Jun18'26 310 CALL', built from the contract fields.
    """
    if c.secType not in _OPTION_TYPES:
        return c.symbol
    exp = c.lastTradeDateOrContractMonth
    try:
        datepart = datetime.strptime(exp, "%Y%m%d").strftime("%b%d'%y")
    except ValueError:
        datepart = exp  # monthly (YYYYMM) or unexpected format -> leave as-is
    return f"{c.symbol} {datepart} {_fmt_strike(c.strike)} {_RIGHT.get(c.right, c.right)}"


def _position_value(pos):
    """Signed quantity as int when whole (400, -520), else the float."""
    return int(pos) if float(pos).is_integer() else pos


def _empty(is_option):
    """Empty-cell marker. PROVISIONAL: a single signifier -- blank "" -- for
    every empty cell, regardless of instrument type.

    We derived a stock->'NoMD' / option->blank rule from his big file, but a
    fresh export from another account placed the markers in the OPPOSITE cells.
    So TWS's NoMD-vs-blank is an unstable, subscription-dependent artifact we
    can't reproduce reliably. Blank is the one marker that appears across every
    instrument type and column in his file, so it's the safest universal empty.
    Pending his confirmation that the distinction doesn't matter, we emit blank.

    To restore the two-marker rule:  return "" if is_option else NOMD
    """
    return ""


def _val(value, is_option, fmt=None):
    """A present value (optionally formatted), else the instrument's empty marker.

    `value` is expected pre-cleaned (NaN/None already collapsed to None), so a
    real 0 / 0.0 is kept, and only genuine absence falls through to _empty().
    """
    if value is None:
        return _empty(is_option)
    return fmt(value) if fmt else value


def _whole(v):
    """Round to a whole number (sizes, share counts, dollar P&L / market value)."""
    return int(round(v))


# --- section assignment & ordering -------------------------------------------

def _section(p):
    if p.contract.secType in _OPTION_TYPES:
        return "OPTION"
    return "LONG" if p.position >= 0 else "SHORT"


def _sort_key(p):
    c = p.contract
    if c.secType in _OPTION_TYPES:
        return (c.symbol, c.lastTradeDateOrContractMonth, float(c.strike or 0), c.right)
    return (c.symbol, "", 0.0, "")


# --- row + frame -------------------------------------------------------------

def _row(p, ticker, single, fee_rate):
    c = p.contract
    opt = c.secType in _OPTION_TYPES         # instrument type, passed to _empty()
    mult = float(c.multiplier) if c.multiplier else 1.0

    last = _price(ticker, "last", "delayedLast")
    close = _clean(getattr(ticker, "close", None))
    change_pct = ((last - close) / close
                  if last is not None and close not in (None, 0) else None)

    def pnl(attr):
        return _clean(getattr(single, attr, None))

    return {
        "Financial Instrument": _instrument_name(c),
        "Delta": _val(_greek(ticker, "delta"), opt, lambda v: round(v, 3)),
        "Bid": _val(_price(ticker, "bid", "delayedBid"), opt),
        "Ask": _val(_price(ticker, "ask", "delayedAsk"), opt),
        "Position": _position_value(p.position),         # always present
        "Avg Price": round(p.avgCost / mult, 6),         # round to kill float-division noise
        "Underlying Price": _val(_greek(ticker, "undPrice"), opt, lambda v: round(v, 2)),
        "Market Value": _val(pnl("value"), opt, _whole),
        "Unrealized P&L": _val(pnl("unrealizedPnL"), opt, _whole),
        "Realized P&L": _val(pnl("realizedPnL"), opt, _whole),
        "Bid Size": _val(_price(ticker, "bidSize", "delayedBidSize"), opt, _whole),
        "Ask Size": _val(_price(ticker, "askSize", "delayedAskSize"), opt, _whole),
        "Last": _val(last, opt),
        "Change %": _val(change_pct, opt),
        "Volume": "",        # known data gap (Volume metric unconfirmed) -- left blank
        "Fee rate": _val(_clean(fee_rate), opt),  # stocks: borrow file; options: blank
        "Shortable Shares": _val(_clean(getattr(ticker, "shortableShares", None)), opt, _whole),
        "Ticker Action": _empty(opt),   # UI-button column, never carries data
        "Opt. Implied Volatility %": _val(_clean(getattr(ticker, "impliedVolatility", None)),
                                          opt, lambda v: f"{v * 100:.1f}%"),
    }


def build_portfolio_df(positions, quotes, pnl, fee_rates=None):
    """Assemble his PricesDeltas layout (3 sections, his exact column order).

    positions: ib.positions() result
    quotes:    {conId: Ticker} from fetch_quotes (needs generic ticks 236, 106)
    pnl:       {conId: PnLSingle} from fetch_pnl (market value + P&L)
    fee_rates: {conId: record} from borrow.fetch_fee_rates, or None/{} if the
               borrow file was unavailable (-> Fee rate stays blank). Stocks only.
    """
    fee_rates = fee_rates or {}
    buckets = {"LONG": [], "SHORT": [], "OPTION": []}
    for p in positions:
        buckets[_section(p)].append(p)

    rows = []
    for section in ("LONG", "SHORT", "OPTION"):
        for p in sorted(buckets[section], key=_sort_key):
            cid = p.contract.conId
            rec = fee_rates.get(cid)
            raw_fee = rec.get("fee") if rec else None
            # Borrow file stores fee as annual PERCENT (AAPL 0.25); his column is the
            # fraction (0.0025). Stocks only -- options have no borrow rate.
            fee = (raw_fee / 100) if (raw_fee is not None
                                      and p.contract.secType not in _OPTION_TYPES) else None
            row = _row(p, quotes.get(cid), pnl.get(cid), fee)
            # None -> "" so missing quote fields render as blank cells, not NaN.
            rows.append({k: ("" if v is None else v) for k, v in row.items()})
    return pd.DataFrame(rows, columns=COLUMNS)


class PortfolioWriter:
    """Writes the boss-format file to its own output_portfolio/portfolio_<timestamp>/ folder."""

    def __init__(self, base_dir: Path | None = None):
        base = Path(base_dir) if base_dir else config.OUTPUT_PORTFOLIO_DIR
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.dir = base / f"portfolio_{stamp}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, df: pd.DataFrame, name: str = "PricesDeltas") -> Path:
        path = self.dir / f"{name}.csv"
        df.to_csv(path, index=False)
        return path
