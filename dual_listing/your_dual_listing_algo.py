import datetime as dt
import time
import logging
from math import floor, ceil

from optibook.synchronous_client import Exchange

exchange = Exchange()
exchange.connect()

logging.getLogger("client").setLevel("ERROR")

PAIRS = [
    ("ASML", "ASML_DUAL"),
    ("TSLA", "TSLA_DUAL"),
]

POSITION_LIMIT = 100
BASE_QUOTE_VOLUME = 40
CREDIT = 0.02
SLEEP_SECONDS = 0.25


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def safe_volume(instrument_id: str, volume: int, side: str) -> int:
    positions = exchange.get_positions()
    pos = positions.get(instrument_id, 0)
    if side == "bid":
        headroom = POSITION_LIMIT - pos
    else:
        headroom = POSITION_LIMIT + pos
    return clamp(volume, 0, headroom)


def get_books(liquid: str, dual: str):
    liq_book = exchange.get_last_price_book(liquid)
    dual_book = exchange.get_last_price_book(dual)
    return liq_book, dual_book


def books_valid(book) -> bool:
    return book and book.bids and book.asks


def active_arbitrage(liquid: str, dual: str):
    liq_book, dual_book = get_books(liquid, dual)
    if not (books_valid(liq_book) and books_valid(dual_book)):
        return

    liq_bid = liq_book.bids[0].price
    liq_ask = liq_book.asks[0].price
    dual_bid = dual_book.bids[0].price
    dual_ask = dual_book.asks[0].price
    dual_ask_vol = dual_book.asks[0].volume
    dual_bid_vol = dual_book.bids[0].volume

    # Buy cheap DUAL, sell expensive liquid
    if dual_ask < liq_bid:
        vol = min(dual_ask_vol, 40)
        buy_vol = safe_volume(dual, vol, "bid")
        sell_vol = safe_volume(liquid, vol, "ask")
        vol = min(buy_vol, sell_vol)
        if vol > 0:
            exchange.insert_order(dual, price=dual_ask, volume=vol, side="bid", order_type="ioc")
            exchange.insert_order(liquid, price=liq_bid, volume=vol, side="ask", order_type="ioc")

    # Sell expensive DUAL, buy cheap liquid
    if dual_bid > liq_ask:
        vol = min(dual_bid_vol, 40)
        sell_vol = safe_volume(dual, vol, "ask")
        buy_vol = safe_volume(liquid, vol, "bid")
        vol = min(sell_vol, buy_vol)
        if vol > 0:
            exchange.insert_order(dual, price=dual_bid, volume=vol, side="ask", order_type="ioc")
            exchange.insert_order(liquid, price=liq_ask, volume=vol, side="bid", order_type="ioc")


def passive_quoting(liquid: str, dual: str):
    exchange.delete_orders(dual)

    liq_book = exchange.get_last_price_book(liquid)
    if not books_valid(liq_book):
        return

    liq_bid = liq_book.bids[0].price
    liq_ask = liq_book.asks[0].price

    positions = exchange.get_positions()
    dual_pos = positions.get(dual, 0)

    # Skew volumes to flatten position: if long, quote heavier on ask side
    bid_vol = clamp(BASE_QUOTE_VOLUME - dual_pos, 0, BASE_QUOTE_VOLUME)
    ask_vol = clamp(BASE_QUOTE_VOLUME + dual_pos, 0, BASE_QUOTE_VOLUME)

    bid_vol = safe_volume(dual, bid_vol, "bid")
    ask_vol = safe_volume(dual, ask_vol, "ask")

    # Round prices away from mid to guarantee credit
    our_bid = floor((liq_bid - CREDIT) * 100) / 100.0
    our_ask = ceil((liq_ask + CREDIT) * 100) / 100.0

    if bid_vol > 0:
        exchange.insert_order(dual, price=our_bid, volume=bid_vol, side="bid", order_type="limit")
    if ask_vol > 0:
        exchange.insert_order(dual, price=our_ask, volume=ask_vol, side="ask", order_type="limit")


def hedge_fills(liquid: str, dual: str):
    positions = exchange.get_positions()
    dual_pos = positions.get(dual, 0)
    liq_pos = positions.get(liquid, 0)

    # Net position across the pair; want it near zero
    net = dual_pos + liq_pos
    if net == 0:
        return

    liq_book = exchange.get_last_price_book(liquid)
    if not books_valid(liq_book):
        return

    if net > 0:
        # We're net long across the pair, sell liquid to flatten
        vol = safe_volume(liquid, abs(net), "ask")
        if vol > 0:
            price = liq_book.bids[0].price
            exchange.insert_order(liquid, price=price, volume=vol, side="ask", order_type="ioc")
    else:
        # We're net short, buy liquid to flatten
        vol = safe_volume(liquid, abs(net), "bid")
        if vol > 0:
            price = liq_book.asks[0].price
            exchange.insert_order(liquid, price=price, volume=vol, side="bid", order_type="ioc")


def print_status():
    positions = exchange.get_positions()
    pnl = exchange.get_pnl()
    all_instruments = [i for pair in PAIRS for i in pair]
    parts = [f"{iid}={positions.get(iid, 0):+d}" for iid in all_instruments]
    print(f"[{dt.datetime.now():%H:%M:%S}] PnL={pnl:.2f}  {' | '.join(parts)}")


iteration = 0

while True:
    try:
        for liquid, dual in PAIRS:
            active_arbitrage(liquid, dual)
            passive_quoting(liquid, dual)
            hedge_fills(liquid, dual)

        if iteration % 20 == 0:
            print_status()
        iteration += 1

        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(1)
