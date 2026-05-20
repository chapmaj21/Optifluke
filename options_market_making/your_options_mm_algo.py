import datetime as dt
import time
import logging

from optibook.synchronous_client import Exchange
from optibook.common_types import InstrumentType, OptionKind

from math import floor, ceil

import sys
import subprocess


def install_and_import(package):
    try:
        __import__(package)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    finally:
        globals()[package] = __import__(package)


install_and_import("scipy")
sys.path.append("/home/workspace/your_optiver_workspace")

from common.black_scholes import call_value, put_value, call_delta, put_delta, call_vega, put_vega
from common.libs import calculate_current_time_to_date

exchange = Exchange()
exchange.connect()

logging.getLogger("client").setLevel("ERROR")

STOCK_ID = "ASML"
SIGMA = 3.0
RATE = 0.03
TICK_SIZE = 0.10
POSITION_LIMIT = 100
QUOTE_VOLUME = 40

MIN_CREDIT = 0.05
VEGA_SCALE = 0.02
SPREAD_SCALE = 0.1
MIN_CREDIT_FLOOR = 0.10
MIN_CREDIT_PCT = 0.04


def round_down_to_tick(price: float, tick_size: float) -> float:
    return floor(price / tick_size) * tick_size


def round_up_to_tick(price: float, tick_size: float) -> float:
    return ceil(price / tick_size) * tick_size


def get_midpoint(instrument_id: str) -> float | None:
    book = exchange.get_last_price_book(instrument_id=instrument_id)
    if not (book and book.bids and book.asks):
        return None
    return (book.bids[0].price + book.asks[0].price) / 2.0


def get_book_spread(instrument_id: str) -> float:
    book = exchange.get_last_price_book(instrument_id=instrument_id)
    if not (book and book.bids and book.asks):
        return 0.0
    return book.asks[0].price - book.bids[0].price


def theoretical_value(S: float, K: float, T: float, r: float, sigma: float, kind: OptionKind) -> float:
    if kind == OptionKind.CALL:
        return call_value(S=S, K=K, T=T, r=r, sigma=sigma)
    elif kind == OptionKind.PUT:
        return put_value(S=S, K=K, T=T, r=r, sigma=sigma)
    raise ValueError(f"Unknown option kind: {kind}")


def option_delta(S: float, K: float, T: float, r: float, sigma: float, kind: OptionKind) -> float:
    if kind == OptionKind.CALL:
        return call_delta(S=S, K=K, T=T, r=r, sigma=sigma)
    elif kind == OptionKind.PUT:
        return put_delta(S=S, K=K, T=T, r=r, sigma=sigma)
    raise ValueError(f"Unknown option kind: {kind}")


def option_vega(S: float, K: float, T: float, r: float, sigma: float, kind: OptionKind) -> float:
    if kind == OptionKind.CALL:
        return call_vega(S=S, K=K, T=T, r=r, sigma=sigma)
    elif kind == OptionKind.PUT:
        return put_vega(S=S, K=K, T=T, r=r, sigma=sigma)
    raise ValueError(f"Unknown option kind: {kind}")


def compute_credit(theo: float, vega: float, market_spread: float) -> float:
    credit = MIN_CREDIT + VEGA_SCALE * abs(vega) + SPREAD_SCALE * market_spread
    credit = max(credit, MIN_CREDIT_PCT * theo)
    credit = max(MIN_CREDIT_FLOOR, credit)
    return credit


def load_options(underlying_id: str) -> dict:
    all_instruments = exchange.get_instruments()
    return {
        iid: inst
        for iid, inst in all_instruments.items()
        if inst.instrument_type == InstrumentType.STOCK_OPTION
        and inst.base_instrument_id == underlying_id
    }


def update_quotes(option_id: str, theo: float, credit: float, position: int) -> None:
    trades = exchange.poll_new_trades(instrument_id=option_id)
    for t in trades:
        print(f"  traded {t.volume} lots {option_id} @ {t.price:.2f} ({t.side})")

    exchange.delete_orders(instrument_id=option_id)

    bid_price = round_down_to_tick(theo - credit, TICK_SIZE)
    ask_price = round_up_to_tick(theo + credit, TICK_SIZE)

    bid_volume = min(QUOTE_VOLUME, POSITION_LIMIT - position)
    ask_volume = min(QUOTE_VOLUME, POSITION_LIMIT + position)

    if bid_volume > 0 and bid_price > 0:
        exchange.insert_order(
            instrument_id=option_id,
            price=bid_price,
            volume=bid_volume,
            side="bid",
            order_type="limit",
        )
    if ask_volume > 0 and ask_price > 0:
        exchange.insert_order(
            instrument_id=option_id,
            price=ask_price,
            volume=ask_volume,
            side="ask",
            order_type="limit",
        )

    print(f"  {option_id}: theo={theo:.2f} credit={credit:.2f} bid={bid_price:.2f}x{bid_volume} ask={ask_price:.2f}x{ask_volume} pos={position}")


def hedge_delta(stock_id: str, options: dict, S: float) -> None:
    positions = exchange.get_positions()
    stock_position = positions.get(stock_id, 0)
    total_delta = float(stock_position)

    for option_id, option in options.items():
        pos = positions.get(option_id, 0)
        if pos == 0:
            continue
        T = calculate_current_time_to_date(option.expiry)
        delta = option_delta(S, option.strike, T, RATE, SIGMA, option.option_kind)
        total_delta += pos * delta

    print(f"  portfolio delta: {total_delta:+.2f} (stock={stock_position})")

    if abs(total_delta) <= 0.5:
        return

    book = exchange.get_last_price_book(instrument_id=stock_id)
    if not (book and book.bids and book.asks):
        print("  no stock book available for hedging")
        return

    if total_delta > 0.5:
        # long delta -> sell stock
        lots = round(total_delta)
        lots = min(lots, POSITION_LIMIT + stock_position)
        if lots > 0:
            price = book.bids[0].price
            exchange.insert_order(
                instrument_id=stock_id,
                price=price,
                volume=lots,
                side="ask",
                order_type="ioc",
            )
            print(f"  hedge: sold {lots} {stock_id} @ {price:.2f}")
    elif total_delta < -0.5:
        lots = round(abs(total_delta))
        lots = min(lots, POSITION_LIMIT - stock_position)
        if lots > 0:
            price = book.asks[0].price
            exchange.insert_order(
                instrument_id=stock_id,
                price=price,
                volume=lots,
                side="bid",
                order_type="ioc",
            )
            print(f"  hedge: bought {lots} {stock_id} @ {price:.2f}")


options = load_options(STOCK_ID)
print(f"loaded {len(options)} options for {STOCK_ID}: {list(options.keys())}")

while True:
    print(f"\n{'='*60}")
    print(f"ITERATION {dt.datetime.now().strftime('%H:%M:%S')} UTC")
    print(f"{'='*60}")

    S = get_midpoint(STOCK_ID)
    if S is None:
        print("no stock book, skipping")
        time.sleep(2)
        continue

    print(f"ASML mid: {S:.2f}")
    positions = exchange.get_positions()

    for option_id, option in options.items():
        T = calculate_current_time_to_date(option.expiry)
        theo = theoretical_value(S, option.strike, T, RATE, SIGMA, option.option_kind)
        vega = option_vega(S, option.strike, T, RATE, SIGMA, option.option_kind)
        spread = get_book_spread(option_id)
        credit = compute_credit(theo, vega, spread)
        pos = positions.get(option_id, 0)

        update_quotes(option_id, theo, credit, pos)
        time.sleep(0.10)

    print(f"\ndelta hedge:")
    hedge_delta(STOCK_ID, options, S)

    pnl = exchange.get_pnl()
    print(f"\nPnL: {pnl:.2f}")
    time.sleep(2)
