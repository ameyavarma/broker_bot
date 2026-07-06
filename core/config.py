"""Central configuration, loaded from the .env file in the project root."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- IBKR connection ---
IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "7497"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))
IB_ACCOUNT = os.getenv("IB_ACCOUNT", "")

# TWS listens on different ports depending on which login it holds
# (standard defaults: live = 7496, paper = 7497). Scripts that ask the user
# "paper or live?" pick between these; IB_PORT above is the fallback for
# scripts that don't ask. Override in .env only for non-standard TWS setups.
IB_PORT_LIVE = int(os.getenv("IB_PORT_LIVE", "7496"))
IB_PORT_PAPER = int(os.getenv("IB_PORT_PAPER", "7497"))

# --- Output ---
# Separate top-level folders per product, for clarity.
OUTPUT_SNAPSHOTS_DIR = Path(os.getenv("OUTPUT_SNAPSHOTS_DIR", "output_snapshots"))
OUTPUT_PORTFOLIO_DIR = Path(os.getenv("OUTPUT_PORTFOLIO_DIR", "output_portfolio"))

# --- Market data ---
WATCHLIST = [s.strip() for s in os.getenv("WATCHLIST", "").split(",") if s.strip()]
