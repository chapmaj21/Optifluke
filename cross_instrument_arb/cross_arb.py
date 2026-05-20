import datetime as dt
import time
import logging
from math import exp, floor, ceil

from optibook.synchronous_client import Exchange
from optibook.common_types import InstrumentType, OptionKind

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

from common.black_scholes import call_value, put_value, call_delta, put_delta
from common.libs import calculate_current_time_to_date

logging.getLogger("client").setLevel("ERROR")
logger = logging.getLogger("cross_arb")
logger.setLevel("INFO")

RATE = 0.03
SIGMA = 3.0
POSITION_LIMIT = 100
ARB_VOLUME = 10
TICK = 0.10

CONSTITUENTS = {"ASML": 908.06, "AAPL": 129.24, "SAP": 124.78, "TSLA": 2245.39, "NVDA": 953.21}
INDEX_DIVISOR = 1000.0
ETF_M = 0.25
ETF_C = 2.50

exchange = Exchange()
exchange.connect()


def clamp_volume(instrument_id: str, side: str, desired: int) -> int:
    pos = exchange.get_positions().get(instrument_id, 0)
    if side == "bid":
        headroom = POSITION_LIMIT - pos
    else:
        headroom = POSITION_LIMIT + pos
    return max(0, min(desired, headroom))


def book_valid(book) -> bool:
    return book and book.bids and book.asks


def get_mid(instrument_id: str) -> float | None:
    book = exchange.get_last_price_book(instrument_id)
    if not book_valid(book):
        return None
    return (book.bids[0].price + book.asks[0].price) / 2.0


def get_book(instrument_id: str):
    return exchange.get_last_price_book(instrument_id)


# ---- Discover instruments ----

instruments = exchange.get_instruments()

futures = {
    iid: inst for iid, inst in instruments.items()
    if iid.endswith("_F") and "OB5X" in iid
}
futures_sorted = sorted(futures.keys(), key=lambda iid: instruments[iid].expiry)

options = {
    iid: inst for iid, inst in instruments.items()
    if inst.instrument_type == InstrumentType.STOCK_OPTION
}

# Group options by (base_instrument, expiry, strike) to find call-put pairs
option_pairs = {}
for iid, inst in options.items():
    key = (inst.base_instrument_id, inst.expiry, inst.strike)
    if key not in option_pairs:
        option_pairs[key] = {}
    option_pairs[key][inst.option_kind] = iid

logger.info(f"Found {len(futures)} futures, {len(options)} options, {len(option_pairs)} call-put pair groups")


# ---- Strategy 1: Put-Call Parity Arbitrage ----
# C - P = S * exp(-d*tau) - K * exp(-r*tau)
# For no dividends on Optibook: C - P = S - K * exp(-r*tau)
# If market violates this, trade the cheap side and hedge

def put_call_parity_arb():
    for (underlying, expiry, strike), kinds in option_pairs.items():
        if OptionKind.CALL not in kinds or OptionKind.PUT not in kinds:
            continue

        call_id = kinds[OptionKind.CALL]
        put_id = kinds[OptionKind.PUT]

        call_book = get_book(call_id)
        put_book = get_book(put_id)
        stock_book = get_book(underlying)

        if not (book_valid(call_book) and book_valid(put_book) and book_valid(stock_book)):
            continue

        tau = calculate_current_time_to_date(expiry)
        if tau <= 0:
            continue

        stock_mid = (stock_book.bids[0].price + stock_book.asks[0].price) / 2.0
        pv_strike = strike * exp(-RATE * tau)

        # Theoretical: C - P = S - K*exp(-r*tau)
        theo_diff = stock_mid - pv_strike

        call_bid = call_book.bids[0].price
        call_ask = call_book.asks[0].price
        put_bid = put_book.bids[0].price
        put_ask = put_book.asks[0].price

        # Synthetic long stock = long call + short put. Cost = call_ask - put_bid
        # Real stock bid = stock_bid. Profit if stock_bid > call_ask - put_bid + pv_strike...
        # Simpler: check if market C - P deviates from theo_diff
        market_call_mid = (call_bid + call_ask) / 2.0
        market_put_mid = (put_bid + put_ask) / 2.0
        market_diff = market_call_mid - market_put_mid

        mispricing = market_diff - theo_diff

        # Calls overpriced relative to puts: sell call, buy put, buy stock
        if mispricing > 0.20:
            vol = ARB_VOLUME
            sell_call_vol = clamp_volume(call_id, "ask", vol)
            buy_put_vol = clamp_volume(put_id, "bid", vol)
            buy_stock_vol = clamp_volume(underlying, "bid", vol)
            vol = min(sell_call_vol, buy_put_vol, buy_stock_vol)
            if vol > 0:
                exchange.insert_order(call_id, price=call_bid, volume=vol, side="ask", order_type="ioc")
                exchange.insert_order(put_id, price=put_ask, volume=vol, side="bid", order_type="ioc")
                delta_call = call_delta(stock_mid, strike, tau, RATE, SIGMA)
                delta_put = put_delta(stock_mid, strike, tau, RATE, SIGMA)
                hedge_lots = round(vol * (delta_call + delta_put))
                if hedge_lots > 0:
                    hedge_vol = clamp_volume(underlying, "bid", hedge_lots)
                    if hedge_vol > 0:
                        exchange.insert_order(underlying, price=stock_book.asks[0].price, volume=hedge_vol, side="bid", order_type="ioc")

        # Calls underpriced relative to puts: buy call, sell put, sell stock
        elif mispricing < -0.20:
            vol = ARB_VOLUME
            buy_call_vol = clamp_volume(call_id, "bid", vol)
            sell_put_vol = clamp_volume(put_id, "ask", vol)
            sell_stock_vol = clamp_volume(underlying, "ask", vol)
            vol = min(buy_call_vol, sell_put_vol, sell_stock_vol)
            if vol > 0:
                exchange.insert_order(call_id, price=call_ask, volume=vol, side="bid", order_type="ioc")
                exchange.insert_order(put_id, price=put_bid, volume=vol, side="ask", order_type="ioc")
                delta_call = call_delta(stock_mid, strike, tau, RATE, SIGMA)
                delta_put = put_delta(stock_mid, strike, tau, RATE, SIGMA)
                hedge_lots = round(vol * abs(delta_call + delta_put))
                if hedge_lots > 0:
                    hedge_vol = clamp_volume(underlying, "ask", hedge_lots)
                    if hedge_vol > 0:
                        exchange.insert_order(underlying, price=stock_book.bids[0].price, volume=hedge_vol, side="ask", order_type="ioc")


# ---- Strategy 2: Stock-Future Basis Arbitrage ----
# F should = S * exp(r * tau). If it deviates, trade the spread.

def stock_future_basis_arb():
    for stock_id in CONSTITUENTS:
        stock_book = get_book(stock_id)
        if not book_valid(stock_book):
            continue

        stock_mid = (stock_book.bids[0].price + stock_book.asks[0].price) / 2.0

        # Check individual stock futures
        future_id = None
        for iid, inst in instruments.items():
            if iid.endswith("_F") and iid.startswith(stock_id):
                future_id = iid
                break

        if future_id is None:
            continue

        fut_book = get_book(future_id)
        if not book_valid(fut_book):
            continue

        tau = calculate_current_time_to_date(instruments[future_id].expiry)
        if tau <= 0:
            continue

        fut_mid = (fut_book.bids[0].price + fut_book.asks[0].price) / 2.0
        fair_future = stock_mid * exp(RATE * tau)
        basis_error = fut_mid - fair_future

        # Future is rich: sell future, buy stock
        if basis_error > 0.10:
            vol = 5
            sell_fut = clamp_volume(future_id, "ask", vol)
            buy_stock = clamp_volume(stock_id, "bid", vol)
            vol = min(sell_fut, buy_stock)
            if vol > 0:
                exchange.insert_order(future_id, price=fut_book.bids[0].price, volume=vol, side="ask", order_type="ioc")
                exchange.insert_order(stock_id, price=stock_book.asks[0].price, volume=vol, side="bid", order_type="ioc")

        # Future is cheap: buy future, sell stock
        elif basis_error < -0.10:
            vol = 5
            buy_fut = clamp_volume(future_id, "bid", vol)
            sell_stock = clamp_volume(stock_id, "ask", vol)
            vol = min(buy_fut, sell_stock)
            if vol > 0:
                exchange.insert_order(future_id, price=fut_book.asks[0].price, volume=vol, side="bid", order_type="ioc")
                exchange.insert_order(stock_id, price=stock_book.bids[0].price, volume=vol, side="ask", order_type="ioc")


# ---- Strategy 3: Index Replication Arb ----
# If the ETF price diverges from what the constituent stocks imply

def index_replication_arb():
    etf_book = get_book("OB5X_ETF")
    if not book_valid(etf_book):
        return

    etf_mid = (etf_book.bids[0].price + etf_book.asks[0].price) / 2.0

    # Compute index from constituents
    index_val = 0.0
    for stock_id, weight in CONSTITUENTS.items():
        mid = get_mid(stock_id)
        if mid is None:
            return
        index_val += weight * mid
    index_val /= INDEX_DIVISOR

    fair_etf = ETF_C + ETF_M * index_val
    mispricing = etf_mid - fair_etf

    # ETF is rich: sell ETF
    if mispricing > 0.03:
        vol = clamp_volume("OB5X_ETF", "ask", 10)
        if vol > 0:
            exchange.insert_order("OB5X_ETF", price=etf_book.bids[0].price, volume=vol, side="ask", order_type="ioc")

    # ETF is cheap: buy ETF
    elif mispricing < -0.03:
        vol = clamp_volume("OB5X_ETF", "bid", 10)
        if vol > 0:
            exchange.insert_order("OB5X_ETF", price=etf_book.asks[0].price, volume=vol, side="bid", order_type="ioc")


# ---- Strategy 4: Global Delta Hedge ----
# Keep total portfolio delta near zero across all instruments

def global_hedge():
    positions = exchange.get_positions()
    total_delta = 0.0

    for iid, pos in positions.items():
        if pos == 0:
            continue

        if iid in options:
            opt = options[iid]
            stock_mid = get_mid(opt.base_instrument_id)
            if stock_mid is None:
                continue
            tau = calculate_current_time_to_date(opt.expiry)
            if tau <= 0:
                continue
            if opt.option_kind == OptionKind.CALL:
                d = call_delta(stock_mid, opt.strike, tau, RATE, SIGMA)
            else:
                d = put_delta(stock_mid, opt.strike, tau, RATE, SIGMA)
            total_delta += pos * d

        elif iid in instruments and instruments[iid].instrument_type == InstrumentType.STOCK:
            total_delta += pos

        elif iid.endswith("_F"):
            # Each future is 1:1 with the underlying (approximately)
            total_delta += pos

    # Only hedge if delta is significant
    if abs(total_delta) < 2:
        return

    # Pick the most liquid stock to hedge with
    for hedge_stock in ["ASML", "NVDA", "TSLA"]:
        book = get_book(hedge_stock)
        if not book_valid(book):
            continue

        if total_delta > 2:
            lots = min(round(total_delta), 20)
            vol = clamp_volume(hedge_stock, "ask", lots)
            if vol > 0:
                exchange.insert_order(hedge_stock, price=book.bids[0].price, volume=vol, side="ask", order_type="ioc")
                return

        elif total_delta < -2:
            lots = min(round(abs(total_delta)), 20)
            vol = clamp_volume(hedge_stock, "bid", lots)
            if vol > 0:
                exchange.insert_order(hedge_stock, price=book.asks[0].price, volume=vol, side="bid", order_type="ioc")
                return


# ---- Main Loop ----

iteration = 0

while True:
    try:
        put_call_parity_arb()
        stock_future_basis_arb()
        index_replication_arb()
        global_hedge()

        if iteration % 30 == 0:
            pnl = exchange.get_pnl()
            positions = exchange.get_positions()
            active = {k: v for k, v in positions.items() if v != 0}
            logger.info(f"[iter {iteration}] PnL={pnl:.2f}  active={active}")

        iteration += 1
        time.sleep(0.5)

    except Exception as e:
        logger.error(f"Error: {e}")
        time.sleep(1)
