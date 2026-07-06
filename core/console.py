"""Tiny console helpers and shared prompts for the entry scripts."""
import itertools
import sys
import threading
import time


def prompt_account_mode(paper_port, live_port):
    """Ask which TWS login to target. Returns (port, is_live)."""
    print("Which account is TWS logged into?")
    print(f"  1) Paper (practice)      -- TWS port {paper_port}")
    print(f"  2) Live (REAL MONEY)     -- TWS port {live_port}")
    while True:
        mode = input("Enter 1 or 2 [default 1]: ").strip() or "1"
        if mode == "1":
            return paper_port, False
        if mode == "2":
            return live_port, True
        print("  Please type 1 or 2.")


def prompt_market_data_type(default="1"):
    """Ask live vs delayed market data. Returns the IBKR market-data type
    (1 = live real-time, 4 = delayed-frozen). `default` picks which choice
    a bare Enter means -- pair it to the account mode ("1" live, "2" paper).
    """
    choices = {
        "1": (1, "Live (real-time) -- for a subscribed trading account"),
        "2": (4, "Delayed (free, ~15 min lag) -- for testing without a subscription"),
    }
    print("\nSelect market data source:")
    print("  1) Live (real-time)  -- requires a market-data subscription")
    print("  2) Delayed (free)    -- ~15 min lag, for testing")
    while True:
        choice = input(f"Enter 1 or 2 [default {default}]: ").strip() or default
        if choice in choices:
            md_type, label = choices[choice]
            print(f"  -> {label}\n")
            return md_type
        print("  Please type 1 or 2.")


class Spinner:
    """An animated "working... /" line for slow spots.

    Usage:
        with Spinner("requesting quotes"):
            ...blocking work...

    Runs in a daemon thread so it keeps animating while the main thread is
    blocked in API calls. When stdout is not a terminal (piped/redirected),
    it degrades to printing a single static line instead of animating.
    """

    def __init__(self, label):
        self.label = label
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if sys.stdout.isatty():
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            print(f"  {self.label}...")
        return self

    def _spin(self):
        for ch in itertools.cycle("|/-\\"):
            if self._stop.is_set():
                return
            sys.stdout.write(f"\r  {self.label}... {ch}")
            sys.stdout.flush()
            time.sleep(0.12)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
            # wipe the spinner line so normal output continues cleanly
            sys.stdout.write("\r" + " " * (len(self.label) + 8) + "\r")
            sys.stdout.flush()
        return False
