"""Minimal IBKR connection test: confirm our code can reach TWS."""
from ib_async import IB

HOST = "127.0.0.1"
PORT = 7497      # paper-trading TWS socket port (live would be 7496)
CLIENT_ID = 1    # any unique integer per connected API client

ib = IB()
try:
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=10)
except Exception as e:
    print(f"Could not connect to TWS at {HOST}:{PORT}.")
    print("  -> Is TWS running and logged into the paper account?")
    print("  -> Is the API enabled (Global Config > API > Settings)?")
    print("  -> If TWS showed an 'accept incoming connection' popup, click Accept and re-run.")
    print(f"Error: {e}")
    raise SystemExit(1)

print(f"Connected:          {ib.isConnected()}")
print(f"TWS server version: {ib.client.serverVersion()}")
print(f"Managed accounts:   {ib.managedAccounts()}")

ib.disconnect()
print("Disconnected cleanly.")
