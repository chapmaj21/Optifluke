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

logging.getLogger("client").setLevel("ERROR")

STOCK_ID = "ASML"
INDEX_FUTURE_ID = "OB5X_202609_F"
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

CONSTITUENTS = {"ASML": 908.06, "AAPL": 129.24, "SAP": 124.78, "TSLA": 2245.39, "NVDA": 953.21}
INDEX_DIVISOR = 1000.0

PER_OPTION_SLEEP = 0.20
MAIN_LOOP_SLEEP = 4.0
RECONNECT_SLEEP = 3.0


def round_down_to_tick(price: float, tick_size: float) -> float:
    return floor(price / tick_size) * tick_size


def round_up_to_tick(price: float, tick_size: float) -> float:
    return ceil(price / tick_size) * tick_size


def get_midpoint(exchange: Exchange, instrument_id: str) -> float | None:
    book = exchange.get_last_price_book(instrument_id=instrument_id)
    if not (book and book.bids and book.asks):
        return None
    return (book.bids[0].price + book.asks[0].price) / 2.0


def get_book_spread(exchange: Exchange, instrument_id: str) -> float:
    book = exchange.get_last_price_book(instrument_id=instrument_id)
    if not (book and book.bids and book.asks):
        return 0.0
    return book.asks[0].price - book.bids[0].price


def compute_index_value(exchange: Exchange) -> float | None:
    total = 0.0
    for stock_id, weight in CONSTITUENTS.items():
        mid = get_midpoint(exchange, stock_id)
        if mid is None:
            return None
        total += weight * mid
    return total / INDEX_DIVISOR


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


def load_options(exchange: Exchange) -> tuple[dict, dict]:
    all_instruments = exchange.get_instruments()
    stock_options = {
        iid: inst
        for iid, inst in all_instruments.items()
        if inst.instrument_type == InstrumentType.STOCK_OPTION
        and inst.base_instrument_id == STOCK_ID
    }
    index_options = {
        iid: inst
        for iid, inst in all_instruments.items()
        if inst.instrument_type == InstrumentType.INDEX_OPTION
    }
    return stock_options, index_options


def update_quotes(exchange: Exchange, option_id: str, theo: float, credit: float, position: int) -> None:
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


def hedge_stock_delta(exchange: Exchange, stock_options: dict, S: float) -> None:
    positions = exchange.get_positions()
    stock_position = positions.get(STOCK_ID, 0)
    total_delta = float(stock_position)

    for option_id, option in stock_options.items():
        pos = positions.get(option_id, 0)
        if pos == 0:
            continue
        T = calculate_current_time_to_date(option.expiry)
        delta = option_delta(S, option.strike, T, RATE, SIGMA, option.option_kind)
        total_delta += pos * delta

    print(f"  ASML portfolio delta: {total_delta:+.2f} (stock={stock_position})")

    if abs(total_delta) <= 0.5:
        return

    book = exchange.get_last_price_book(instrument_id=STOCK_ID)
    if not (book and book.bids and book.asks):
        print("  no stock book for hedging")
        return

    if total_delta > 0.5:
        lots = round(total_delta)
        lots = min(lots, POSITION_LIMIT + stock_position)
        if lots > 0:
            price = book.bids[0].price
            exchange.insert_order(
                instrument_id=STOCK_ID,
                price=price,
                volume=lots,
                side="ask",
                order_type="ioc",
            )
            print(f"  hedge: sold {lots} {STOCK_ID} @ {price:.2f}")
    elif total_delta < -0.5:
        lots = round(abs(total_delta))
        lots = min(lots, POSITION_LIMIT - stock_position)
        if lots > 0:
            price = book.asks[0].price
            exchange.insert_order(
                instrument_id=STOCK_ID,
                price=price,
                volume=lots,
                side="bid",
                order_type="ioc",
            )
            print(f"  hedge: bought {lots} {STOCK_ID} @ {price:.2f}")


def hedge_index_delta(exchange: Exchange, index_options: dict, index_value: float) -> None:
    positions = exchange.get_positions()
    future_position = positions.get(INDEX_FUTURE_ID, 0)
    total_delta = float(future_position)

    for option_id, option in index_options.items():
        pos = positions.get(option_id, 0)
        if pos == 0:
            continue
        T = calculate_current_time_to_date(option.expiry)
        delta = option_delta(index_value, option.strike, T, RATE, SIGMA, option.option_kind)
        total_delta += pos * delta

    print(f"  OB5X portfolio delta: {total_delta:+.2f} (future={future_position})")

    if abs(total_delta) <= 0.5:
        return

    book = exchange.get_last_price_book(instrument_id=INDEX_FUTURE_ID)
    if not (book and book.bids and book.asks):
        print("  no future book for index hedging")
        return

    if total_delta > 0.5:
        lots = round(total_delta)
        lots = min(lots, POSITION_LIMIT + future_position)
        if lots > 0:
            price = book.bids[0].price
            exchange.insert_order(
                instrument_id=INDEX_FUTURE_ID,
                price=price,
                volume=lots,
                side="ask",
                order_type="ioc",
            )
            print(f"  hedge: sold {lots} {INDEX_FUTURE_ID} @ {price:.2f}")
    elif total_delta < -0.5:
        lots = round(abs(total_delta))
        lots = min(lots, POSITION_LIMIT - future_position)
        if lots > 0:
            price = book.asks[0].price
            exchange.insert_order(
                instrument_id=INDEX_FUTURE_ID,
                price=price,
                volume=lots,
                side="bid",
                order_type="ioc",
            )
            print(f"  hedge: bought {lots} {INDEX_FUTURE_ID} @ {price:.2f}")


def create_and_connect() -> Exchange:
    e = Exchange()
    e.connect()
    return e


exchange = create_and_connect()
stock_options, index_options = load_options(exchange)
print(f"loaded {len(stock_options)} stock options: {list(stock_options.keys())}")
print(f"loaded {len(index_options)} index options: {list(index_options.keys())}")

while True:
    if not exchange.is_connected():
        print("disconnected, reconnecting...")
        exchange = create_and_connect()
        stock_options, index_options = load_options(exchange)
        print(f"reconnected. stock_opts={len(stock_options)} index_opts={len(index_options)}")
        time.sleep(RECONNECT_SLEEP)
        continue

    print(f"\n{'='*60}")
    print(f"ITERATION {dt.datetime.now().strftime('%H:%M:%S')} UTC")
    print(f"{'='*60}")

    S = get_midpoint(exchange, STOCK_ID)
    if S is None:
        print("no ASML book, skipping")
        time.sleep(MAIN_LOOP_SLEEP)
        continue

    index_value = compute_index_value(exchange)

    print(f"ASML mid: {S:.2f}", end="")
    if index_value is not None:
        print(f"  OB5X index: {index_value:.2f}")
    else:
        print("  OB5X index: unavailable")

    positions = exchange.get_positions()

    for option_id, option in stock_options.items():
        T = calculate_current_time_to_date(option.expiry)
        theo = theoretical_value(S, option.strike, T, RATE, SIGMA, option.option_kind)
        vega = option_vega(S, option.strike, T, RATE, SIGMA, option.option_kind)
        spread = get_book_spread(exchange, option_id)
        credit = compute_credit(theo, vega, spread)
        pos = positions.get(option_id, 0)

        update_quotes(exchange, option_id, theo, credit, pos)
        time.sleep(PER_OPTION_SLEEP)

    if index_value is not None:
        for option_id, option in index_options.items():
            T = calculate_current_time_to_date(option.expiry)
            theo = theoretical_value(index_value, option.strike, T, RATE, SIGMA, option.option_kind)
            vega = option_vega(index_value, option.strike, T, RATE, SIGMA, option.option_kind)
            spread = get_book_spread(exchange, option_id)
            credit = compute_credit(theo, vega, spread)
            pos = positions.get(option_id, 0)

            update_quotes(exchange, option_id, theo, credit, pos)
            time.sleep(PER_OPTION_SLEEP)

    print(f"\ndelta hedge (stock):")
    hedge_stock_delta(exchange, stock_options, S)

    if index_value is not None:
        print(f"delta hedge (index):")
        hedge_index_delta(exchange, index_options, index_value)

    pnl = exchange.get_pnl()
    print(f"\nPnL: {pnl:.2f}")
    time.sleep(MAIN_LOOP_SLEEP)
