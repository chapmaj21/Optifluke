import datetime as dt
import time
import logging
from math import floor, ceil

from optibook.synchronous_client import Exchange

logging.getLogger("client").setLevel("ERROR")

POSITION_LIMIT = 100
BASE_QUOTE_VOLUME = 40
CREDIT = 0.02
SLEEP_SECONDS = 1.0


def connect():
    e = Exchange()
    e.connect()
    return e


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def books_valid(book) -> bool:
    return book and book.bids and book.asks


def discover_dual_pairs(exchange) -> list[tuple[str, str]]:
    instruments = exchange.get_instruments()
    all_ids = set(instruments.keys())
    pairs = []
    for iid in sorted(all_ids):
        dual_id = iid + "_DUAL"
        if dual_id in all_ids:
            pairs.append((iid, dual_id))
    if not pairs:
        print(f"WARNING: no dual pairs found among {sorted(all_ids)}")
    else:
        print(f"discovered dual pairs: {pairs}")
    return pairs


def safe_volume(exchange, instrument_id: str, volume: int, side: str) -> int:
    pos = exchange.get_positions().get(instrument_id, 0)
    if side == "bid":
        headroom = POSITION_LIMIT - pos
    else:
        headroom = POSITION_LIMIT + pos
    return clamp(volume, 0, headroom)


def active_arbitrage(exchange, liquid: str, dual: str):
    liq_book = exchange.get_last_price_book(liquid)
    dual_book = exchange.get_last_price_book(dual)
    if not (books_valid(liq_book) and books_valid(dual_book)):
        return

    liq_bid = liq_book.bids[0].price
    liq_ask = liq_book.asks[0].price
    dual_bid = dual_book.bids[0].price
    dual_ask = dual_book.asks[0].price

    if dual_ask < liq_bid:
        vol = min(dual_book.asks[0].volume, 40)
        vol = min(safe_volume(exchange, dual, vol, "bid"), safe_volume(exchange, liquid, vol, "ask"))
        if vol > 0:
            exchange.insert_order(dual, price=dual_ask, volume=vol, side="bid", order_type="ioc")
            exchange.insert_order(liquid, price=liq_bid, volume=vol, side="ask", order_type="ioc")

    if dual_bid > liq_ask:
        vol = min(dual_book.bids[0].volume, 40)
        vol = min(safe_volume(exchange, dual, vol, "ask"), safe_volume(exchange, liquid, vol, "bid"))
        if vol > 0:
            exchange.insert_order(dual, price=dual_bid, volume=vol, side="ask", order_type="ioc")
            exchange.insert_order(liquid, price=liq_ask, volume=vol, side="bid", order_type="ioc")


def passive_quoting(exchange, liquid: str, dual: str):
    exchange.delete_orders(dual)

    liq_book = exchange.get_last_price_book(liquid)
    if not books_valid(liq_book):
        return

    liq_bid = liq_book.bids[0].price
    liq_ask = liq_book.asks[0].price

    dual_pos = exchange.get_positions().get(dual, 0)

    bid_vol = clamp(BASE_QUOTE_VOLUME - dual_pos, 0, BASE_QUOTE_VOLUME)
    ask_vol = clamp(BASE_QUOTE_VOLUME + dual_pos, 0, BASE_QUOTE_VOLUME)
    bid_vol = safe_volume(exchange, dual, bid_vol, "bid")
    ask_vol = safe_volume(exchange, dual, ask_vol, "ask")

    our_bid = floor((liq_bid - CREDIT) * 100) / 100.0
    our_ask = ceil((liq_ask + CREDIT) * 100) / 100.0

    if bid_vol > 0:
        exchange.insert_order(dual, price=our_bid, volume=bid_vol, side="bid", order_type="limit")
    if ask_vol > 0:
        exchange.insert_order(dual, price=our_ask, volume=ask_vol, side="ask", order_type="limit")


def hedge_fills(exchange, liquid: str, dual: str):
    positions = exchange.get_positions()
    net = positions.get(dual, 0) + positions.get(liquid, 0)
    if net == 0:
        return

    liq_book = exchange.get_last_price_book(liquid)
    if not books_valid(liq_book):
        return

    if net > 0:
        vol = safe_volume(exchange, liquid, abs(net), "ask")
        if vol > 0:
            exchange.insert_order(liquid, price=liq_book.bids[0].price, volume=vol, side="ask", order_type="ioc")
    else:
        vol = safe_volume(exchange, liquid, abs(net), "bid")
        if vol > 0:
            exchange.insert_order(liquid, price=liq_book.asks[0].price, volume=vol, side="bid", order_type="ioc")


exchange = connect()
PAIRS = discover_dual_pairs(exchange)

iteration = 0

while True:
    try:
        if not exchange.is_connected():
            print("reconnecting...")
            exchange = connect()
            PAIRS = discover_dual_pairs(exchange)
            time.sleep(2)
            continue

        for liquid, dual in PAIRS:
            active_arbitrage(exchange, liquid, dual)
            passive_quoting(exchange, liquid, dual)
            hedge_fills(exchange, liquid, dual)
            time.sleep(0.05)

        if iteration % 20 == 0:
            positions = exchange.get_positions()
            pnl = exchange.get_pnl()
            parts = [f"{iid}={positions.get(iid, 0):+d}" for pair in PAIRS for iid in pair]
            print(f"[{dt.datetime.now():%H:%M:%S}] PnL={pnl:.2f}  {' | '.join(parts)}")
        iteration += 1

        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(3)
