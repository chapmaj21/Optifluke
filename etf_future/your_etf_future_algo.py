import time
import logging
from math import exp, floor, ceil

from optibook.synchronous_client import Exchange
from optibook.common_types import InstrumentType

import sys
sys.path.append("/home/workspace/your_optiver_workspace")
from common.libs import calculate_current_time_to_date

logging.getLogger("client").setLevel("ERROR")
logger = logging.getLogger("algo")
logger.setLevel("INFO")

ETF_ID = "OB5X_ETF"
CONSTITUENTS = {"ASML": 908.06, "AAPL": 129.24, "SAP": 124.78, "TSLA": 2245.39, "NVDA": 953.21}
INDEX_DIVISOR = 1000.0

ETF_M = 0.25
ETF_C = 2.50
RISK_FREE_RATE = 0.03

ETF_TICK = 0.10
POSITION_LIMIT = 100
MAX_QUOTE_VOLUME = 40
CREDIT = 0.01

SLEEP_S = 4.0


def index_from_constituents(exchange) -> float | None:
    total = 0.0
    for stock, weight in CONSTITUENTS.items():
        book = exchange.get_last_price_book(stock)
        if not book or not book.bids or not book.asks:
            return None
        mid = (book.bids[0].price + book.asks[0].price) / 2.0
        total += weight * mid
    return total / INDEX_DIVISOR


def future_price_to_index(future_price: float, tau: float) -> float:
    return future_price / exp(RISK_FREE_RATE * tau)


def index_to_etf(index_value: float) -> float:
    return ETF_C + ETF_M * index_value


def round_down_tick(price: float, tick: float) -> float:
    return floor(price / tick) * tick


def round_up_tick(price: float, tick: float) -> float:
    return ceil(price / tick) * tick


def clamp_volume(instrument_id: str, side: str, desired: int, positions: dict) -> int:
    pos = positions.get(instrument_id, 0)
    if side == "bid":
        headroom = POSITION_LIMIT - pos
    else:
        headroom = POSITION_LIMIT + pos
    return max(0, min(desired, headroom))


def discover_futures(exchange) -> list[str]:
    instruments = exchange.get_instruments()
    fids = sorted(
        [iid for iid, inst in instruments.items() if "OB5X" in iid and iid.endswith("_F")],
        key=lambda iid: instruments[iid].expiry,
    )
    if not fids:
        raise RuntimeError("No OB5X futures found")
    return fids


exchange = Exchange()
exchange.connect()

instruments = exchange.get_instruments()
future_ids = discover_futures(exchange)
PRIMARY_FUTURE = future_ids[0]
logger.info(f"Primary future: {PRIMARY_FUTURE}, all futures: {future_ids}")

constituent_index: float | None = None
iteration = 0

while True:
    iteration += 1

    try:
        if not exchange.is_connected():
            logger.warning("Disconnected, reconnecting...")
            exchange = Exchange()
            exchange.connect()
            instruments = exchange.get_instruments()
            future_ids = discover_futures(exchange)
            PRIMARY_FUTURE = future_ids[0]
            time.sleep(3.0)
            continue

        positions = exchange.get_positions()
        pnl = exchange.get_pnl()

        if iteration % 20 == 1:
            pos_str = ", ".join(f"{k}={v}" for k, v in positions.items() if v != 0)
            logger.info(f"[iter {iteration}] PnL={pnl:.2f}  positions: {pos_str or 'flat'}")

        # Pull future book for primary future (1 API call)
        future_book = exchange.get_last_price_book(PRIMARY_FUTURE)
        if not future_book or not future_book.bids or not future_book.asks:
            time.sleep(SLEEP_S)
            continue

        tau = calculate_current_time_to_date(instruments[PRIMARY_FUTURE].expiry)
        if tau <= 0:
            tau = 1e-6

        fut_bid = future_book.bids[0].price
        fut_ask = future_book.asks[0].price
        fut_mid = (fut_bid + fut_ask) / 2.0

        # Constituent index: 5 API calls, only every 5th iteration
        if iteration % 5 == 1:
            constituent_index = index_from_constituents(exchange)

        future_implied_index = future_price_to_index(fut_mid, tau)

        if constituent_index is not None:
            index_mid = (constituent_index + future_implied_index) / 2.0
        else:
            index_mid = future_implied_index

        # ETF fair value from future bid/ask
        etf_fair_bid = index_to_etf(future_price_to_index(fut_bid, tau))
        etf_fair_ask = index_to_etf(future_price_to_index(fut_ask, tau))

        if constituent_index is not None:
            etf_from_constituent = index_to_etf(constituent_index)
            etf_fair_bid = min(etf_fair_bid, etf_from_constituent)
            etf_fair_ask = max(etf_fair_ask, etf_from_constituent)

        etf_bid_price = round_down_tick(etf_fair_bid - CREDIT, ETF_TICK)
        etf_ask_price = round_up_tick(etf_fair_ask + CREDIT, ETF_TICK)

        if etf_bid_price <= 0 or etf_ask_price <= 0 or etf_bid_price >= etf_ask_price:
            time.sleep(SLEEP_S)
            continue

        # Position-aware quoting volumes
        etf_pos = positions.get(ETF_ID, 0)
        bid_vol = clamp_volume(ETF_ID, "bid", MAX_QUOTE_VOLUME, positions)
        ask_vol = clamp_volume(ETF_ID, "ask", MAX_QUOTE_VOLUME, positions)

        if etf_pos > 10:
            bid_vol = max(1, bid_vol - etf_pos // 2)
        elif etf_pos < -10:
            ask_vol = max(1, ask_vol + etf_pos // 2)

        # Clear and re-quote ETF (3 API calls: delete + 2 inserts)
        exchange.delete_orders(ETF_ID)

        if bid_vol > 0:
            exchange.insert_order(ETF_ID, price=etf_bid_price, volume=bid_vol, side="bid", order_type="limit")
        if ask_vol > 0:
            exchange.insert_order(ETF_ID, price=etf_ask_price, volume=ask_vol, side="ask", order_type="limit")

        # Delta hedge (1-2 API calls)
        positions = exchange.get_positions()
        etf_pos = positions.get(ETF_ID, 0)
        fut_pos = positions.get(PRIMARY_FUTURE, 0)

        target_fut = -round(ETF_M * etf_pos)
        hedge_needed = target_fut - fut_pos

        if hedge_needed != 0:
            if hedge_needed > 0:
                hedge_side = "bid"
                hedge_price = fut_ask
            else:
                hedge_side = "ask"
                hedge_price = fut_bid

            hedge_vol = clamp_volume(PRIMARY_FUTURE, hedge_side, abs(hedge_needed), positions)
            if hedge_vol > 0:
                exchange.insert_order(
                    PRIMARY_FUTURE,
                    price=hedge_price,
                    volume=hedge_vol,
                    side=hedge_side,
                    order_type="ioc",
                )

        # Cross-future arb: expensive, only every 10th iteration
        if iteration % 10 == 0 and len(future_ids) >= 2:
            for i in range(len(future_ids)):
                for j in range(i + 1, len(future_ids)):
                    fid_near = future_ids[i]
                    fid_far = future_ids[j]

                    book_near = exchange.get_last_price_book(fid_near)
                    book_far = exchange.get_last_price_book(fid_far)
                    if not (book_near and book_near.bids and book_near.asks):
                        continue
                    if not (book_far and book_far.bids and book_far.asks):
                        continue

                    tau_near = calculate_current_time_to_date(instruments[fid_near].expiry)
                    tau_far = calculate_current_time_to_date(instruments[fid_far].expiry)

                    near_mid = (book_near.bids[0].price + book_near.asks[0].price) / 2.0
                    far_mid = (book_far.bids[0].price + book_far.asks[0].price) / 2.0

                    fair_ratio = exp(RISK_FREE_RATE * (tau_far - tau_near))
                    fair_far = near_mid * fair_ratio

                    if far_mid > fair_far + 0.05:
                        arb_vol = 5
                        buy_vol = clamp_volume(fid_near, "bid", arb_vol, positions)
                        sell_vol = clamp_volume(fid_far, "ask", arb_vol, positions)
                        vol = min(buy_vol, sell_vol)
                        if vol > 0:
                            exchange.insert_order(fid_near, price=book_near.asks[0].price, volume=vol, side="bid", order_type="ioc")
                            exchange.insert_order(fid_far, price=book_far.bids[0].price, volume=vol, side="ask", order_type="ioc")

                    elif far_mid < fair_far - 0.05:
                        arb_vol = 5
                        sell_vol = clamp_volume(fid_near, "ask", arb_vol, positions)
                        buy_vol = clamp_volume(fid_far, "bid", arb_vol, positions)
                        vol = min(buy_vol, sell_vol)
                        if vol > 0:
                            exchange.insert_order(fid_near, price=book_near.bids[0].price, volume=vol, side="ask", order_type="ioc")
                            exchange.insert_order(fid_far, price=book_far.asks[0].price, volume=vol, side="bid", order_type="ioc")

        # Constituent vs future arb: only every 5th iteration (same cadence as constituent fetch)
        if iteration % 5 == 1 and constituent_index is not None:
            fut_implied = future_price_to_index(fut_mid, tau)
            spread = constituent_index - fut_implied

            if spread > 0.10:
                arb_vol = 5
                etf_book = exchange.get_last_price_book(ETF_ID)
                if etf_book and etf_book.bids:
                    sell_vol = clamp_volume(ETF_ID, "ask", arb_vol, positions)
                    if sell_vol > 0:
                        exchange.insert_order(ETF_ID, price=etf_book.bids[0].price, volume=sell_vol, side="ask", order_type="ioc")
                        hedge_buy = clamp_volume(PRIMARY_FUTURE, "bid", max(1, round(ETF_M * sell_vol)), positions)
                        if hedge_buy > 0:
                            exchange.insert_order(PRIMARY_FUTURE, price=fut_ask, volume=hedge_buy, side="bid", order_type="ioc")

            elif spread < -0.10:
                arb_vol = 5
                etf_book = exchange.get_last_price_book(ETF_ID)
                if etf_book and etf_book.asks:
                    buy_vol = clamp_volume(ETF_ID, "bid", arb_vol, positions)
                    if buy_vol > 0:
                        exchange.insert_order(ETF_ID, price=etf_book.asks[0].price, volume=buy_vol, side="bid", order_type="ioc")
                        hedge_sell = clamp_volume(PRIMARY_FUTURE, "ask", max(1, round(ETF_M * buy_vol)), positions)
                        if hedge_sell > 0:
                            exchange.insert_order(PRIMARY_FUTURE, price=fut_bid, volume=hedge_sell, side="ask", order_type="ioc")

    except Exception as e:
        logger.error(f"Error in iteration {iteration}: {e}")

    time.sleep(SLEEP_S)
