"""Take a point-in-time snapshot of the IBKR account and write it to CSV.

Run it:  python snapshot.py
"""
import sys
from pathlib import Path

# Internal modules live in core/; put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))

import connection
import fetchers
from export_snapshot import SnapshotWriter


def main():
    try:
        with connection.connect() as ib:
            print(f"Connected to {ib.managedAccounts()}. Pulling data...")
            datasets = {
                "account_summary": fetchers.fetch_account_summary(ib),
                "positions": fetchers.fetch_positions(ib),
                "trades": fetchers.fetch_trades(ib),
            }
    except Exception as e:
        print("Could not connect to / pull from TWS.")
        print("  Is TWS running, logged into paper, with the API enabled?")
        print(f"  Details: {e}")
        raise SystemExit(1)

    writer = SnapshotWriter()
    for name, df in datasets.items():
        path = writer.write(df, name)
        print(f"  {name:16s} {len(df):4d} rows -> {path.name}")
    print(f"\nSnapshot complete: {writer.dir}")


if __name__ == "__main__":
    main()
