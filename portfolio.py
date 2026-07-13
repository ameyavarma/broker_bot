"""Entry point for the boss-format (PricesDeltas) portfolio snapshot.

Parallel to snapshot.py, but produces his single formatted file in its own
output folder. Shares the same foundation (config / connection / fetchers).

    python portfolio.py            # runs with the HARDCODED settings below
    python portfolio.py --prompts  # asks interactively instead
"""
import sys
from pathlib import Path

# Internal modules live in core/; put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))

import connection
import config
import console
import fetchers
import borrow  # kept for the disabled H+ block (Fee rate) in main()
from export_portfolio import build_portfolio_df, PortfolioWriter


# Default run settings -- edit here to change what a plain "python portfolio.py"
# does. Run with --prompts to be asked interactively instead.
HARDCODED = dict(
    live=True,          # True = live TWS (port 7496), False = paper (7497)
    account="U1281286", # account whose positions to pull ("" = all accounts)
    md_type=1,          # market data: 1 = live real-time, 4 = delayed (free)
)


def main():
    if "--prompts" in sys.argv[1:]:
        port, live = console.prompt_account_mode(config.IB_PORT_PAPER,
                                                 config.IB_PORT_LIVE)
        account = console.prompt_account_id(default=config.IB_ACCOUNT)
        md_type = console.prompt_market_data_type(default="1" if live else "2")
    else:
        live = HARDCODED["live"]
        port = config.IB_PORT_LIVE if live else config.IB_PORT_PAPER
        account = HARDCODED["account"]
        md_type = HARDCODED["md_type"]
        print("Hardcoded settings: "
              f"{'LIVE' if live else 'paper'} TWS (port {port}), "
              f"account {account or 'ALL'}, "
              f"{'real-time' if md_type == 1 else 'delayed'} market data.\n"
              "(Run \"python portfolio.py --prompts\" to choose interactively.)")
    try:
        with connection.connect(port=port) as ib:
            ib.reqMarketDataType(md_type)
            accounts = ib.managedAccounts()
            # Advisor logins manage dozens of accounts; don't dump them all.
            shown = accounts if len(accounts) <= 8 else f"{len(accounts)} accounts"
            which = f"positions for {account}" if account else "positions (all accounts)"
            print(f"Connected to {shown}. Pulling {which}...")
            if account and account not in accounts:
                print(f"  WARNING: {account} is not among this login's "
                      f"accounts -- the result will be empty. Check the ID.")
            positions = ib.positions(account)
            contracts = [p.contract for p in positions]
            print(f"  {len(positions)} positions; requesting market data "
                  f"(type {md_type})...")
            quotes = fetchers.fetch_quotes(ib, contracts,
                                           progress=lambda m: print(f"  {m}"))

            # --- H+ data DISABLED -------------------------------------------------
            # Output is trimmed to columns A-G (see COLUMNS in export_portfolio.py),
            # so we skip the fetches only needed for columns H onward: market value /
            # P&L, the borrow fee rate (an FTP download), and the extra ticks for
            # shortable shares / implied vol. To re-enable, add generic_ticks="236,106"
            # to fetch_quotes above and uncomment the block below.
            pnl, fee_rates = {}, {}
            # print("  requesting market value / P&L...")
            # pnl = fetchers.fetch_pnl(ib, positions)
            # print("  fetching borrow fee rates...")
            # try:
            #     fee_rates = borrow.fetch_fee_rates()
            #     print(f"    {len(fee_rates)} symbols from borrow file")
            # except Exception as e:
            #     print(f"    borrow file unavailable ({type(e).__name__}); Fee rate blank")
            #     fee_rates = {}
            # ----------------------------------------------------------------------
            df = build_portfolio_df(positions, quotes, pnl, fee_rates)
    except Exception as e:
        print("Could not connect to / pull from TWS.")
        print("  Is TWS running, logged in, with the API enabled?")
        print(f"  Details: {e}")
        raise SystemExit(1)

    writer = PortfolioWriter()
    path = writer.write(df)
    print(f"\n{len(df)} rows -> {path}\n")
    print(df.to_string(index=False))
    print(f"\nPortfolio snapshot complete: {writer.dir}")


if __name__ == "__main__":
    main()
