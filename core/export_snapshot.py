"""Writer: persist DataFrames as a point-in-time snapshot of CSV files.

A SnapshotWriter creates one timestamped folder; each DataFrame written goes
into that folder as <name>.csv. Knows nothing about IBKR -- pure persistence.
"""
from datetime import datetime
from pathlib import Path

import pandas as pd

import config


class SnapshotWriter:
    """Creates a timestamped snapshot folder and writes CSVs into it."""

    def __init__(self, base_dir: Path | None = None):
        base = Path(base_dir) if base_dir else config.OUTPUT_SNAPSHOTS_DIR
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.dir = base / f"snapshot_{stamp}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, df: pd.DataFrame, name: str) -> Path:
        path = self.dir / f"{name}.csv"
        df.to_csv(path, index=False)
        return path
