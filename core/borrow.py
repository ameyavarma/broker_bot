"""Fetch stock borrow fee rates from IBKR's public securities-lending file.

The borrow fee rate (TWS 'Fee rate' column) is NOT available through the market-
data API -- it lives in IBKR's Securities Lending system. IBKR publishes it as a
public file on their FTP server (the same data that feeds TWS), so we download
and parse it, keyed by conId. No market-data subscription needed.

File: served from IBKR FTP mirror hosts (see FTP_HOSTS), one per region (usa.txt).
Pipe-delimited, e.g.:
    #SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|
    AAPL|USD|APPLE INC|265598|US0378331005|3.37|0.25|>10000000|
    #EOF|12345
Lines starting with '#' are header/trailer; everything else is a record.
FEERATE/REBATERATE are annual PERCENTAGES (AAPL FEERATE 0.25 = 0.25%/yr); TWS's
'Fee rate' column shows the fraction (0.0025), so callers divide by 100.

Only stocks appear here -- options have no borrow data (their 'Fee rate' stays
blank), which matches his file.
"""
import ftplib
import io

# IBKR mirrors this file across several FTP hosts; some go intermittently
# unreachable (e.g. ftp3/ftp were down on subnet 206.106.137.x while ftp2 was
# up), so we try them in order. ftp2 first -- confirmed reachable.
FTP_HOSTS = (
    "ftp2.interactivebrokers.com",
    "ftp3.interactivebrokers.com",
    "ftp.interactivebrokers.com",
)
FTP_USER = "shortstock"
FTP_PASS = "shortstock"

_FIELDS = ("SYM", "CUR", "NAME", "CON", "ISIN", "REBATERATE", "FEERATE", "AVAILABLE")


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _download(filename, hosts=FTP_HOSTS):
    """Pull the file over FTP into a string, trying each mirror host until one
    connects (latin-1: names may carry non-UTF8 bytes)."""
    last_err = None
    for host in hosts:
        try:
            buf = io.BytesIO()
            with ftplib.FTP(host, timeout=30) as ftp:
                ftp.login(FTP_USER, FTP_PASS)
                ftp.retrbinary(f"RETR {filename}", buf.write)
            return buf.getvalue().decode("latin-1")
        except Exception as exc:
            last_err = exc
    raise last_err if last_err else RuntimeError("no FTP hosts configured")


def parse(text):
    """Parse the pipe-delimited text -> {conId: {symbol, fee, rebate, available}}."""
    out = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) < len(_FIELDS):
            continue
        rec = dict(zip(_FIELDS, parts))
        try:
            con_id = int(rec["CON"])
        except (ValueError, KeyError):
            continue
        out[con_id] = {
            "symbol": rec["SYM"],
            "fee": _num(rec["FEERATE"]),
            "rebate": _num(rec["REBATERATE"]),
            "available": rec["AVAILABLE"],
        }
    return out


def fetch_fee_rates(filename="usa.txt"):
    """Download + parse the securities-lending file. Returns {conId: record}."""
    return parse(_download(filename))
