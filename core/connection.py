"""Manages the connection to TWS / IB Gateway."""
import logging
import re
from contextlib import contextmanager

from ib_async import IB

import config

# --- benign market-data noise -------------------------------------------------
# On accounts without an API market-data subscription (e.g. our paper account),
# IBKR answers every quote request with a loud entitlement error, then falls
# back to the free delayed feed and delivers the data anyway (see
# fetchers.fetch_quotes). The codes below are that bounce -- expected, harmless,
# and alarming-looking -- so we swallow them and print one short note instead.
_BENIGN_MD_CODES = {
    10089: ("no API market-data subscription for this feed; "
            "IBKR falls back to free delayed data, so values still arrive"),
    10090: ("part of the quote isn't covered by a subscription; "
            "the rest arrives via the free delayed feed"),
    10091: ("part of the quote isn't covered by a subscription; "
            "the rest arrives via the free delayed feed"),
    10167: "delayed market data is now flowing",
}


class _QuietBenignMarketDataErrors(logging.Filter):
    """Silence the benign entitlement errors above on ib_async's logger,
    printing a one-line explanation the first time each code appears."""

    def __init__(self):
        super().__init__()
        self._seen = set()

    def filter(self, record):
        m = re.match(r"(?:Error|Warning) (\d+),", record.getMessage())
        if not m or int(m.group(1)) not in _BENIGN_MD_CODES:
            return True  # not ours -- let it through untouched
        code = int(m.group(1))
        if code not in self._seen:
            self._seen.add(code)
            print(f"  note: IBKR sent expected error {code} -- "
                  f"{_BENIGN_MD_CODES[code]}.")
        return False


# ib_async logs these through the "ib_async.wrapper" logger; filters only fire
# on the logger they are attached to, so attach exactly there, once per process.
logging.getLogger("ib_async.wrapper").addFilter(_QuietBenignMarketDataErrors())


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
