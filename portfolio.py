"""Entry point for the boss-format (PricesDeltas) portfolio snapshot.

Parallel to snapshot.py, but produces his single formatted file in its own
output folder. Shares the same foundation (config / connection / fetchers).

    python portfolio.py
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


def main():
    port, live = console.prompt_account_mode(config.IB_PORT_PAPER,
                                             config.IB_PORT_LIVE)
    md_type = console.prompt_market_data_type(default="1" if live else "2")
    try:
        with connection.connect(port=port) as ib:
            ib.reqMarketDataType(md_type)
            print(f"Connected to {ib.managedAccounts()}. Pulling positions...")
            positions = ib.positions(config.IB_ACCOUNT)
            contracts = [p.contract for p in positions]
            print(f"  {len(positions)} positions; requesting market data "
                  f"(type {md_type})...")
            quotes = fetchers.fetch_quotes(ib, contracts)

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
