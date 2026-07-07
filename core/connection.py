"""Manages the connection to TWS / IB Gateway."""
import logging
import re
from contextlib import contextmanager

from ib_async import IB

import config

# --- benign IBKR noise ----------------------------------------------------------
# Errors IBKR sends that look alarming but are expected and harmless, swallowed
# and replaced with one short explanatory note each.
#
# 10089/10090/10091/10167: on accounts without an API market-data subscription
# (e.g. our paper account), IBKR answers every quote request with a loud
# entitlement error, then falls back to the free delayed feed and delivers the
# data anyway (see fetchers.fetch_quotes).
# 10275: on multi-account (advisor) logins, positions for a client account
# whose IBKR application hasn't been approved yet simply aren't reported;
# every other account still comes through in full.
_BENIGN_CODES = {
    10089: ("no API market-data subscription for this feed; "
            "IBKR falls back to free delayed data, so values still arrive"),
    10090: ("part of the quote isn't covered by a subscription; "
            "the rest arrives via the free delayed feed"),
    10091: ("part of the quote isn't covered by a subscription; "
            "the rest arrives via the free delayed feed"),
    10167: "delayed market data is now flowing",
    10275: ("positions for the account(s) below can't be reported until "
            "their IBKR application is approved; all other accounts are "
            "pulled normally"),
}


class _QuietBenignIbkrErrors(logging.Filter):
    """Silence the benign errors above on ib_async's logger, printing a
    one-line explanation the first time each code appears."""

    def __init__(self):
        super().__init__()
        self._seen = set()

    def filter(self, record):
        m = re.match(r"(?:Error|Warning) (\d+),", record.getMessage())
        if not m or int(m.group(1)) not in _BENIGN_CODES:
            return True  # not ours -- let it through untouched
        code = int(m.group(1))
        if code not in self._seen:
            self._seen.add(code)
            extra = ""
            if code == 10275:  # keep the affected-accounts detail visible
                acc = re.search(r"account\(s\): *(\w+(?:, *\w+)*)",
                                record.getMessage())
                if acc:
                    extra = f" (affected: {acc.group(1)})"
            print(f"  note: IBKR sent expected error {code} -- "
                  f"{_BENIGN_CODES[code]}{extra}.")
        return False


# ib_async logs these through the "ib_async.wrapper" logger; filters only fire
# on the logger they are attached to, so attach exactly there, once per process.
logging.getLogger("ib_async.wrapper").addFilter(_QuietBenignIbkrErrors())


@contextmanager
def connect(port=None):
    """Open a connection to TWS and guarantee a clean disconnect.

    port: TWS port to target (e.g. config.IB_PORT_LIVE / IB_PORT_PAPER);
    defaults to config.IB_PORT.

    Usage:
        with connect() as ib:
            ...use ib...
    """
    ib = IB()
    try:
        ib.connect(
            config.IB_HOST,
            port or config.IB_PORT,
            clientId=config.IB_CLIENT_ID,
            timeout=10,
        )
    except TimeoutError:
        # TWS regularly lets the first API handshake after a previous session
        # time out, then accepts an immediate retry. Absorb one timeout here.
        ib.connect(
            config.IB_HOST,
            port or config.IB_PORT,
            clientId=config.IB_CLIENT_ID,
            timeout=10,
        )
    try:
        yield ib
    finally:
        ib.disconnect()
