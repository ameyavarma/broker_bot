"""Entry point for the boss's option-selling functions (OptionFunctions.txt):
SellNearestOption and SellOutOfMoneyOption.

Flow: interactive prompts (first: which function) -> compute the order plan ->
PREVIEW (every candidate leg with keep/skip reason, the DELTA_SUM sizing math,
the exact orders and their premium) -> explicit "yes" -> transmit -> per-order
status report. Nothing is sent without the confirmation step.

Transmit modes: "send" transmits immediately; "stage in TWS" places every
order with transmit=False (TWS holds them on the API tab with a Transmit
button, nothing reaches the exchange until clicked); "basket CSV" places no
orders at all -- it writes a TWS BasketTrader import file matching the
boss's hand-made basket CSVs.

    python options_order.py            # live TWS / his account / real-time
                                       # (see HARDCODED below)
    python options_order.py --prompts  # ask those three interactively too
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Internal modules live in core/; put it on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent / "core"))

import basket_export
import config
import connection
import console
import option_strategies
import order_exec

# Default run settings -- used unless started with --prompts, which asks for
# these three interactively (all trade inputs are always prompted).
HARDCODED = dict(
    live=True,           # True = live TWS (port 7496), False = paper (7497)
    account="U1281286",  # account the orders target / basket Account column
    md_type=1,           # market data: 1 = live real-time, 4 = delayed (free)
)


# --- prompts ------------------------------------------------------------------

def _prompt(label, parse, example):
    """Ask until `parse` accepts the input; show the example on bad input."""
    while True:
        raw = input(f"{label}: ").strip()
        try:
            return parse(raw)
        except Exception:
            print(f"  Sorry, couldn't read that -- e.g. {example}")


def _parse_ticker(raw):
    if not raw.isalpha():
        raise ValueError
    return raw.upper()


def _parse_right(raw):
    r = raw.upper()
    if r in ("P", "PUT", "PUTS"):
        return "P"
    if r in ("C", "CALL", "CALLS"):
        return "C"
    raise ValueError


def _parse_iv(raw):
    """'10%', '10' and '0.10' all mean ten percent."""
    v = float(raw.rstrip("%"))
    return v / 100 if v > 1 or raw.endswith("%") else v


def _parse_int(raw):
    v = int(raw)
    if v <= 0:
        raise ValueError
    return v


def _parse_expire(raw):
    if not raw:
        return None  # DAY: unfilled orders die at the session close
    v = int(raw)
    if v <= 0:
        raise ValueError
    return v


def prompt_inputs():
    interactive = "--prompts" in sys.argv[1:]
    if interactive:
        port, live = console.prompt_account_mode(config.IB_PORT_PAPER,
                                                 config.IB_PORT_LIVE)
        account = console.prompt_account_id(default=HARDCODED["account"],
                                            what="trade")
        md_type = None  # asked at the end, after the trade inputs
    else:
        live = HARDCODED["live"]
        port = config.IB_PORT_LIVE if live else config.IB_PORT_PAPER
        account = HARDCODED["account"]
        md_type = HARDCODED["md_type"]
        print("Hardcoded settings: "
              f"{'LIVE' if live else 'paper'} TWS (port {port}), "
              f"account {account}, "
              f"{'real-time' if md_type == 1 else 'delayed'} market data.\n"
              "(Run \"python options_order.py --prompts\" to choose these "
              "interactively.)")
    print()
    print("Which function?")
    print("  1) SellNearestOption    -- the ATM strike, each expiry (3 months)")
    print("  2) SellOutOfMoneyOption -- the N nearest OTM strikes, each expiry")
    while True:
        func = input("Enter 1 or 2: ").strip()
        if func in ("1", "2"):
            break
        print("  Please type 1 or 2.")
    print()
    ticker = _prompt("Ticker (e.g. LABU)", _parse_ticker, "LABU")
    right = _prompt("Put or Call [P/C]", _parse_right, "P")
    num = _prompt("NUM_CONTRACTS (delta target, e.g. 129)", _parse_int, "129")
    ivt = _prompt("IMPLIED_VOLATILITY_THRESHOLD (e.g. 10%)", _parse_iv, "10%")
    num_otm = None
    if func == "2":
        num_otm = _prompt("NUM_OUT_OF_MONEY (OTM strikes per expiry, e.g. 3)",
                          _parse_int, "3")
    expire = _prompt("Order lifetime in minutes [Enter = rest of day]",
                     _parse_expire, "30")
    mode = _prompt_transmit_mode()
    if md_type is None:
        # Default the data feed to the natural pairing: real-time on a live
        # (subscribed) account, free delayed on paper. Both stay selectable.
        md_type = console.prompt_market_data_type(default="1" if live else "2")
    return (port, func, ticker, right, num, ivt, num_otm, expire, mode,
            account, md_type)


def _prompt_transmit_mode():
    """How the plan leaves this script. Returns "send", "stage" or "csv"."""
    print("\nTransmit mode:")
    print("  1) Send to exchange now")
    print("  2) Stage in TWS only -- orders appear in TWS (API tab) with a")
    print("     Transmit button; NOTHING is sent until you click it per order")
    print("  3) Basket CSV only  -- NO orders placed; writes a file to load")
    print("     into TWS BasketTrader yourself")
    modes = {"1": "send", "2": "stage", "3": "csv"}
    while True:
        m = input("Enter 1, 2 or 3 [default 1]: ").strip() or "1"
        if m in modes:
            return modes[m]
        print("  Please type 1, 2 or 3.")


# --- preview ------------------------------------------------------------------

def _f(v, spec=".2f"):
    return "-" if v is None else format(v, spec)


def _expiry(e):
    """20260710 -> 2026-07-10."""
    return f"{e[:4]}-{e[4:6]}-{e[6:]}"


def print_plan(plan, expire_minutes=None, mode="send", account=None):
    right_word = "PUT" if plan.right == "P" else "CALL"
    print(f"\n=== {plan.strategy} plan: SELL {plan.ticker} {right_word}s ===")
    if account:
        print(f"  target account: {account}")
    otm = f"   OTM strikes/expiry: {plan.num_otm}" if plan.num_otm else ""
    print(f"  spot: {plan.spot} (underlying '{plan.spot_source}')   "
          f"expiries through {plan.cutoff}   IV threshold: {plan.iv_threshold:.1%}"
          f"{otm}")
    print(f"\n  {'expiry':10} {'strike':>8} {'bid':>7} {'ask':>7} "
          f"{'IV':>7} {'delta':>7}  decision")
    for leg in plan.legs:
        iv = f"{leg.iv:.1%}" if leg.iv is not None else "-"
        mark = "KEEP" if leg.status == option_strategies.SELECTED \
            else f"skip: {leg.status}"
        print(f"  {_expiry(leg.expiry):10} {_f(leg.strike, 'g'):>8} {_f(leg.bid):>7} "
              f"{_f(leg.ask):>7} {iv:>7} {_f(leg.delta, '.3f'):>7}  {mark}")

    n = len(plan.selected)
    if not n:
        print("\n  No expiry passed the filters -- nothing to sell.")
        if any("no ask price" in leg.status for leg in plan.legs):
            print("  Hint: missing asks usually mean the options market is "
                  "closed (US equity options trade 9:30-16:00 ET only; "
                  "outside those hours the delayed feed has no quotes).")
        return
    print(f"\n  {n} legs selected")
    print(f"  DELTA_SUM = {plan.delta_sum:.3f}"
          + ("  (puts: negative; |sum| used for sizing)" if plan.delta_sum < 0 else ""))
    print(f"  NUM_ORDER_CONTRACTS = {plan.num_contracts} / {abs(plan.delta_sum):.3f}"
          f" = {plan.num_contracts / abs(plan.delta_sum):.2f}"
          f"  -> {plan.qty_per_leg} contracts per leg")

    print(f"\n  Orders to transmit ({n}):")
    total_premium = 0.0
    for leg in plan.selected:
        mult = float(leg.contract.multiplier or 100)
        price = order_exec.sell_price(leg)
        premium = plan.qty_per_leg * price * mult
        total_premium += premium
        print(f"    SELL {plan.qty_per_leg:>4}  {plan.ticker} {_expiry(leg.expiry)} "
              f"{leg.strike:g} {right_word}   LMT {price:.2f}"
              f"   (premium if filled ~ ${premium:,.0f})")
    print(f"  Total: {plan.qty_per_leg * n} contracts, "
          f"~${total_premium:,.0f} premium if everything fills at the limit.")
    if order_exec.SELL_PRICE_OVERRIDE is not None:
        print(f"  *** SELL PRICE OVERRIDE ACTIVE: every limit is a flat "
              f"{order_exec.SELL_PRICE_OVERRIDE:g}, NOT the ask ***\n"
              "  *** -- placeholder orders; reprice in TWS before "
              "transmitting for real. ***")
    if expire_minutes:
        cancel_at = datetime.now() + timedelta(minutes=expire_minutes)
        print(f"  Unfilled orders auto-cancel {expire_minutes} minutes after "
              f"transmission (GTD, ~{cancel_at.strftime('%H:%M:%S')} your time).")
    else:
        print("  Unfilled orders stay working until today's session close (DAY).")
    print("  Note: sell-limit AT the ask is passive -- fills need a buyer at "
          "that price; away from market hours orders rest until the session opens.")
    if mode == "stage":
        print("\n  STAGED MODE: orders will be HELD at TWS (API tab), each "
              "with a\n  Transmit button. Nothing reaches the exchange until "
              "you click\n  Transmit per order. A TWS restart discards held "
              "orders.")
    elif mode == "csv":
        print("\n  BASKET CSV MODE: NO orders are placed by this script. It "
              "writes a\n  BasketTrader import file; nothing reaches IBKR "
              "until you load it in\n  TWS (New Window -> BasketTrader) and "
              "transmit there.")
    if mode in ("stage", "csv") and expire_minutes:
        print(f"  NOTE: the {expire_minutes}-minute auto-cancel clock is "
              f"fixed NOW (at ~{cancel_at.strftime('%H:%M:%S')});\n  an "
              "order transmitted after that time expires immediately.")

    dropped = [leg for leg in plan.legs if leg.data_gap]
    if dropped:
        print(f"\n  *** WARNING: {len(dropped)} candidate leg(s) DROPPED for "
              "MISSING MARKET DATA (not by the IV filter):")
        for leg in dropped:
            print(f"      {_expiry(leg.expiry)} -- {leg.status}")
        print("      Sizing excludes them, so this ladder is PARTIAL. "
              "If that's unexpected, re-run instead of transmitting.")


# --- confirmation gate -----------------------------------------------------------

def confirm_transmission(paper, plan, mode="send"):
    """The gate between preview and transmission. Paper: type 'yes'. Live:
    a REAL MONEY banner, and the user must retype the total contract count
    from the preview -- a slow-down-and-look guard against autopilot.
    Staged/CSV: a simple 'yes' even on live -- nothing transmits; the real
    gate becomes clicking Transmit in TWS / loading the basket file."""
    if mode == "csv":
        answer = input("\nWrite the basket CSV (no orders placed)? "
                       "Type yes: ").strip().lower()
        return answer == "yes"
    if mode == "stage":
        answer = input("\nStage these orders in TWS (nothing is sent to the "
                       "exchange)? Type yes: ").strip().lower()
        return answer == "yes"
    if paper:
        answer = input("\nTransmit these orders? Type yes to send: ").strip().lower()
        if answer != "yes":
            return False
        return True
    total = plan.qty_per_leg * len(plan.selected)
    print(f"\n*** LIVE ACCOUNT -- these orders commit REAL MONEY ***")
    print(f"*** {len(plan.selected)} orders x {plan.qty_per_leg} contracts each "
          f"= {total} contracts total, sold short. ***")
    answer = input(f"To confirm, retype the total contract count ({total}): ").strip()
    return answer == str(total)


# --- main ----------------------------------------------------------------------

def main():
    (port, func, ticker, right, num, ivt, num_otm, expire, mode, account,
     md_type) = prompt_inputs()
    try:
        with connection.connect(port=port) as ib:
            ib.reqMarketDataType(md_type)
            # (managedAccounts can contain empty strings on some setups)
            accounts = [a for a in ib.managedAccounts() if a]
            # IBKR paper account ids start with 'D' (DU/DF...); live ones don't.
            paper = bool(accounts) and all(a.startswith("D") for a in accounts)
            print(f"Connected to {accounts} "
                  f"({'paper account' if paper else 'LIVE ACCOUNT -- REAL MONEY'}).")
            kwargs = dict(progress=lambda m: print(f"  {m}"),
                          spinner=console.Spinner)
            if func == "1":
                plan = option_strategies.sell_nearest_option(
                    ib, ticker, right, num, ivt, **kwargs)
            else:
                plan = option_strategies.sell_out_of_money_option(
                    ib, ticker, right, num, ivt, num_otm, **kwargs)
            print_plan(plan, expire, mode, account)
            if not plan.selected:
                return
            if plan.qty_per_leg <= 0:
                print("\nComputed quantity is 0 -- nothing to transmit.")
                return

            if not confirm_transmission(paper, plan, mode):
                print("Not confirmed -- nothing was transmitted.")
                return

            if mode == "csv":
                path = basket_export.write_basket_csv(plan, account, expire)
                print(f"\nBasket file written: {path}")
                print("Load it in TWS: New Window -> BasketTrader -> open "
                      "that file, review, then Transmit.")
                return

            # The orders must name an account this login actually manages;
            # otherwise IBKR rejects them. Fall back to the TWS default
            # (loudly) rather than transmitting a doomed order.
            order_account = account
            if account and account not in accounts:
                print(f"\n  NOTE: {account} is not on this TWS login -- "
                      "placing on the login's default account instead.")
                order_account = None

            print("\nStaging..." if mode == "stage" else "\nTransmitting...")
            results = order_exec.execute_sell_plan(ib, plan, expire_minutes=expire,
                                                   staged=(mode == "stage"),
                                                   account=order_account)
            if mode == "stage":
                print("Orders staged (held at TWS, NOT sent to the exchange):")
                for leg, trade in results:
                    print(f"  {plan.ticker} {_expiry(leg.expiry)} "
                          f"{leg.strike:g} {right}  staged "
                          f"(order id {trade.order.orderId})")
                print("\nIn TWS: API -> click Transmit to send, or "
                      "Cancel/discard.\nHeld orders survive this "
                      "script exiting but are LOST if TWS restarts.")
                return
            print("Order status:")
            for leg, trade in results:
                print(f"  {plan.ticker} {_expiry(leg.expiry)} {leg.strike:g} {right}  "
                      f"{order_exec.trade_report_row(trade)}")
            print("\nOrders not yet filled remain working at IBKR "
                  "(check the TWS Orders tab).")
    except (ConnectionRefusedError, TimeoutError):
        print("Could not connect to TWS. Is it running, logged into the "
              "account you selected, with the API enabled?")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
