import datetime as dt
import time
import logging
from math import exp, floor, ceil

from optibook.synchronous_client import Exchange
from optibook.common_types import InstrumentType, OptionKind

import sys
sys.path.append("/home/workspace/your_optiver_workspace")

from common.black_scholes import call_value, put_value, call_delta, put_delta, call_vega, put_vega
from common.libs import calculate_current_time_to_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)-8s] %(message)s", datefmt="%H:%M:%S")
logging.getLogger("client").setLevel("ERROR")
log = logging.getLogger("run")

# ---- Constants ----

POSITION_LIMIT = 100
RATE = 0.03
SIGMA = 3.0
TICK = 0.10

CONSTITUENTS = {"ASML": 908.06, "AAPL": 129.24, "SAP": 124.78, "TSLA": 2245.39, "NVDA": 953.21}
INDEX_DIVISOR = 1000.0
ETF_M = 0.25
ETF_C = 2.50

DUAL_CREDIT = 0.02
DUAL_VOLUME = 40
ETF_CREDIT = 0.01
ETF_VOLUME = 40
OPTION_VOLUME = 40

MIN_CREDIT = 0.05
VEGA_SCALE = 0.02
SPREAD_SCALE = 0.1
MIN_CREDIT_FLOOR = 0.10
MIN_CREDIT_PCT = 0.04


# ---- Helpers ----

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def book_ok(book) -> bool:
    return book and book.bids and book.asks


def get_mid(e: Exchange, iid: str) -> float | None:
    b = e.get_last_price_book(iid)
    if not book_ok(b):
        return None
    return (b.bids[0].price + b.asks[0].price) / 2.0


def vol_headroom(pos: int, side: str) -> int:
    if side == "bid":
        return max(0, POSITION_LIMIT - pos)
    return max(0, POSITION_LIMIT + pos)


def round_down(price: float, tick: float) -> float:
    return floor(price / tick) * tick


def round_up(price: float, tick: float) -> float:
    return ceil(price / tick) * tick


def compute_index(e: Exchange) -> float | None:
    total = 0.0
    for sid, w in CONSTITUENTS.items():
        mid = get_mid(e, sid)
        if mid is None:
            return None
        total += w * mid
    return total / INDEX_DIVISOR


def bs_value(S, K, T, r, sigma, kind):
    if kind == OptionKind.CALL:
        return call_value(S=S, K=K, T=T, r=r, sigma=sigma)
    return put_value(S=S, K=K, T=T, r=r, sigma=sigma)


def bs_delta(S, K, T, r, sigma, kind):
    if kind == OptionKind.CALL:
        return call_delta(S=S, K=K, T=T, r=r, sigma=sigma)
    return put_delta(S=S, K=K, T=T, r=r, sigma=sigma)


def bs_vega(S, K, T, r, sigma, kind):
    if kind == OptionKind.CALL:
        return call_vega(S=S, K=K, T=T, r=r, sigma=sigma)
    return put_vega(S=S, K=K, T=T, r=r, sigma=sigma)


def option_credit(theo: float, vega: float, spread: float) -> float:
    c = MIN_CREDIT + VEGA_SCALE * abs(vega) + SPREAD_SCALE * spread
    c = max(c, MIN_CREDIT_PCT * theo)
    return max(MIN_CREDIT_FLOOR, c)


# ---- Strategy: Dual Listing ----

def run_dual_listing(e: Exchange, pairs: list, positions: dict):
    for liquid, dual in pairs:
        liq_book = e.get_last_price_book(liquid)
        dual_book = e.get_last_price_book(dual)
        if not (book_ok(liq_book) and book_ok(dual_book)):
            continue

        liq_bid = liq_book.bids[0].price
        liq_ask = liq_book.asks[0].price
        dual_bid = dual_book.bids[0].price
        dual_ask = dual_book.asks[0].price

        # Active arb
        if dual_ask < liq_bid:
            v = min(dual_book.asks[0].volume, 40)
            v = min(v, vol_headroom(positions.get(dual, 0), "bid"),
                    vol_headroom(positions.get(liquid, 0), "ask"))
            if v > 0:
                e.insert_order(dual, price=dual_ask, volume=v, side="bid", order_type="ioc")
                e.insert_order(liquid, price=liq_bid, volume=v, side="ask", order_type="ioc")

        if dual_bid > liq_ask:
            v = min(dual_book.bids[0].volume, 40)
            v = min(v, vol_headroom(positions.get(dual, 0), "ask"),
                    vol_headroom(positions.get(liquid, 0), "bid"))
            if v > 0:
                e.insert_order(dual, price=dual_bid, volume=v, side="ask", order_type="ioc")
                e.insert_order(liquid, price=liq_ask, volume=v, side="bid", order_type="ioc")

        # Passive quoting on dual
        e.delete_orders(dual)
        dual_pos = positions.get(dual, 0)
        bid_vol = clamp(DUAL_VOLUME - dual_pos, 0, DUAL_VOLUME)
        ask_vol = clamp(DUAL_VOLUME + dual_pos, 0, DUAL_VOLUME)
        bid_vol = min(bid_vol, vol_headroom(dual_pos, "bid"))
        ask_vol = min(ask_vol, vol_headroom(dual_pos, "ask"))

        our_bid = floor((liq_bid - DUAL_CREDIT) * 10) / 10.0
        our_ask = ceil((liq_ask + DUAL_CREDIT) * 10) / 10.0

        if bid_vol > 0:
            e.insert_order(dual, price=our_bid, volume=bid_vol, side="bid", order_type="limit")
        if ask_vol > 0:
            e.insert_order(dual, price=our_ask, volume=ask_vol, side="ask", order_type="limit")

        # Hedge pair imbalance
        net = positions.get(dual, 0) + positions.get(liquid, 0)
        if net != 0:
            liq_book = e.get_last_price_book(liquid)
            if book_ok(liq_book):
                if net > 0:
                    hv = min(abs(net), vol_headroom(positions.get(liquid, 0), "ask"))
                    if hv > 0:
                        e.insert_order(liquid, price=liq_book.bids[0].price, volume=hv, side="ask", order_type="ioc")
                else:
                    hv = min(abs(net), vol_headroom(positions.get(liquid, 0), "bid"))
                    if hv > 0:
                        e.insert_order(liquid, price=liq_book.asks[0].price, volume=hv, side="bid", order_type="ioc")


# ---- Strategy: ETF-Future ----

def run_etf_future(e: Exchange, primary_future: str, instruments: dict, positions: dict,
                   constituent_index: float | None):
    fut_book = e.get_last_price_book(primary_future)
    if not book_ok(fut_book):
        return

    tau = calculate_current_time_to_date(instruments[primary_future].expiry)
    if tau <= 0:
        tau = 1e-6

    fut_bid = fut_book.bids[0].price
    fut_ask = fut_book.asks[0].price

    etf_fair_bid = ETF_C + ETF_M * (fut_bid / exp(RATE * tau))
    etf_fair_ask = ETF_C + ETF_M * (fut_ask / exp(RATE * tau))

    if constituent_index is not None:
        etf_from_const = ETF_C + ETF_M * constituent_index
        etf_fair_bid = min(etf_fair_bid, etf_from_const)
        etf_fair_ask = max(etf_fair_ask, etf_from_const)

    bid_price = round_down(etf_fair_bid - ETF_CREDIT, TICK)
    ask_price = round_up(etf_fair_ask + ETF_CREDIT, TICK)

    if bid_price <= 0 or ask_price <= 0 or bid_price >= ask_price:
        return

    etf_pos = positions.get("OB5X_ETF", 0)
    bid_vol = min(ETF_VOLUME, vol_headroom(etf_pos, "bid"))
    ask_vol = min(ETF_VOLUME, vol_headroom(etf_pos, "ask"))

    if etf_pos > 10:
        bid_vol = max(1, bid_vol - etf_pos // 2)
    elif etf_pos < -10:
        ask_vol = max(1, ask_vol + etf_pos // 2)

    e.delete_orders("OB5X_ETF")
    if bid_vol > 0:
        e.insert_order("OB5X_ETF", price=bid_price, volume=bid_vol, side="bid", order_type="limit")
    if ask_vol > 0:
        e.insert_order("OB5X_ETF", price=ask_price, volume=ask_vol, side="ask", order_type="limit")

    # Hedge: M * etf_pos + fut_pos = 0
    fut_pos = positions.get(primary_future, 0)
    target_fut = -round(ETF_M * etf_pos)
    hedge = target_fut - fut_pos
    if hedge != 0:
        side = "bid" if hedge > 0 else "ask"
        price = fut_ask if hedge > 0 else fut_bid
        hv = min(abs(hedge), vol_headroom(fut_pos, side))
        if hv > 0:
            e.insert_order(primary_future, price=price, volume=hv, side=side, order_type="ioc")


# ---- Strategy: Options Market Making ----

def run_options_mm(e: Exchange, stock_options: dict, index_options: dict,
                   S: float, index_value: float | None, positions: dict):
    # Quote ASML stock options
    for oid, opt in stock_options.items():
        T = calculate_current_time_to_date(opt.expiry)
        if T <= 0:
            continue
        theo = bs_value(S, opt.strike, T, RATE, SIGMA, opt.option_kind)
        vega = bs_vega(S, opt.strike, T, RATE, SIGMA, opt.option_kind)

        opt_book = e.get_last_price_book(oid)
        spread = (opt_book.asks[0].price - opt_book.bids[0].price) if book_ok(opt_book) else 0.0

        credit = option_credit(theo, vega, spread)
        pos = positions.get(oid, 0)

        e.delete_orders(oid)
        bp = round_down(theo - credit, TICK)
        ap = round_up(theo + credit, TICK)
        bv = min(OPTION_VOLUME, vol_headroom(pos, "bid"))
        av = min(OPTION_VOLUME, vol_headroom(pos, "ask"))

        if bv > 0 and bp > 0:
            e.insert_order(oid, price=bp, volume=bv, side="bid", order_type="limit")
        if av > 0 and ap > 0:
            e.insert_order(oid, price=ap, volume=av, side="ask", order_type="limit")
        time.sleep(0.10)

    # Quote OB5X index options
    if index_value is not None:
        for oid, opt in index_options.items():
            T = calculate_current_time_to_date(opt.expiry)
            if T <= 0:
                continue
            theo = bs_value(index_value, opt.strike, T, RATE, SIGMA, opt.option_kind)
            vega = bs_vega(index_value, opt.strike, T, RATE, SIGMA, opt.option_kind)

            opt_book = e.get_last_price_book(oid)
            spread = (opt_book.asks[0].price - opt_book.bids[0].price) if book_ok(opt_book) else 0.0

            credit = option_credit(theo, vega, spread)
            pos = positions.get(oid, 0)

            e.delete_orders(oid)
            bp = round_down(theo - credit, TICK)
            ap = round_up(theo + credit, TICK)
            bv = min(OPTION_VOLUME, vol_headroom(pos, "bid"))
            av = min(OPTION_VOLUME, vol_headroom(pos, "ask"))

            if bv > 0 and bp > 0:
                e.insert_order(oid, price=bp, volume=bv, side="bid", order_type="limit")
            if av > 0 and ap > 0:
                e.insert_order(oid, price=ap, volume=av, side="ask", order_type="limit")
            time.sleep(0.10)


def hedge_stock_options(e: Exchange, stock_options: dict, S: float, positions: dict):
    stock_pos = positions.get("ASML", 0)
    total_delta = float(stock_pos)

    for oid, opt in stock_options.items():
        pos = positions.get(oid, 0)
        if pos == 0:
            continue
        T = calculate_current_time_to_date(opt.expiry)
        if T <= 0:
            continue
        total_delta += pos * bs_delta(S, opt.strike, T, RATE, SIGMA, opt.option_kind)

    if abs(total_delta) <= 0.5:
        return

    book = e.get_last_price_book("ASML")
    if not book_ok(book):
        return

    if total_delta > 0.5:
        lots = min(round(total_delta), vol_headroom(stock_pos, "ask"))
        if lots > 0:
            e.insert_order("ASML", price=book.bids[0].price, volume=lots, side="ask", order_type="ioc")
    elif total_delta < -0.5:
        lots = min(round(abs(total_delta)), vol_headroom(stock_pos, "bid"))
        if lots > 0:
            e.insert_order("ASML", price=book.asks[0].price, volume=lots, side="bid", order_type="ioc")


def hedge_index_options(e: Exchange, index_options: dict, index_value: float,
                        positions: dict, future_id: str):
    fut_pos = positions.get(future_id, 0)
    total_delta = float(fut_pos)

    for oid, opt in index_options.items():
        pos = positions.get(oid, 0)
        if pos == 0:
            continue
        T = calculate_current_time_to_date(opt.expiry)
        if T <= 0:
            continue
        total_delta += pos * bs_delta(index_value, opt.strike, T, RATE, SIGMA, opt.option_kind)

    if abs(total_delta) <= 0.5:
        return

    book = e.get_last_price_book(future_id)
    if not book_ok(book):
        return

    if total_delta > 0.5:
        lots = min(round(total_delta), vol_headroom(fut_pos, "ask"))
        if lots > 0:
            e.insert_order(future_id, price=book.bids[0].price, volume=lots, side="ask", order_type="ioc")
    elif total_delta < -0.5:
        lots = min(round(abs(total_delta)), vol_headroom(fut_pos, "bid"))
        if lots > 0:
            e.insert_order(future_id, price=book.asks[0].price, volume=lots, side="bid", order_type="ioc")


# ---- Strategy: Cross-Instrument Arb (runs less frequently) ----

def run_cross_arb(e: Exchange, instruments: dict, option_pairs: dict,
                  asml_futures: list, positions: dict):
    # Put-call parity on ASML stock options
    for (underlying, expiry, strike), kinds in option_pairs.items():
        if OptionKind.CALL not in kinds or OptionKind.PUT not in kinds:
            continue
        call_id = kinds[OptionKind.CALL]
        put_id = kinds[OptionKind.PUT]

        cb = e.get_last_price_book(call_id)
        pb = e.get_last_price_book(put_id)
        sb = e.get_last_price_book(underlying)
        if not (book_ok(cb) and book_ok(pb) and book_ok(sb)):
            continue

        tau = calculate_current_time_to_date(expiry)
        if tau <= 0:
            continue

        s_mid = (sb.bids[0].price + sb.asks[0].price) / 2.0
        theo_diff = s_mid - strike * exp(-RATE * tau)
        mkt_diff = (cb.bids[0].price + cb.asks[0].price) / 2.0 - (pb.bids[0].price + pb.asks[0].price) / 2.0
        mispricing = mkt_diff - theo_diff

        if abs(mispricing) > 0.20:
            vol = 10
            if mispricing > 0:
                v = min(vol, vol_headroom(positions.get(call_id, 0), "ask"),
                        vol_headroom(positions.get(put_id, 0), "bid"),
                        vol_headroom(positions.get(underlying, 0), "bid"))
                if v > 0:
                    e.insert_order(call_id, price=cb.bids[0].price, volume=v, side="ask", order_type="ioc")
                    e.insert_order(put_id, price=pb.asks[0].price, volume=v, side="bid", order_type="ioc")
            else:
                v = min(vol, vol_headroom(positions.get(call_id, 0), "bid"),
                        vol_headroom(positions.get(put_id, 0), "ask"),
                        vol_headroom(positions.get(underlying, 0), "ask"))
                if v > 0:
                    e.insert_order(call_id, price=cb.asks[0].price, volume=v, side="bid", order_type="ioc")
                    e.insert_order(put_id, price=pb.bids[0].price, volume=v, side="ask", order_type="ioc")

    # Stock-future basis (ASML only)
    sb = e.get_last_price_book("ASML")
    if book_ok(sb):
        s_mid = (sb.bids[0].price + sb.asks[0].price) / 2.0
        for fid in asml_futures:
            if fid not in instruments:
                continue
            fb = e.get_last_price_book(fid)
            if not book_ok(fb):
                continue
            tau = calculate_current_time_to_date(instruments[fid].expiry)
            if tau <= 0:
                continue
            f_mid = (fb.bids[0].price + fb.asks[0].price) / 2.0
            basis = f_mid - s_mid * exp(RATE * tau)
            if abs(basis) > 0.10:
                v = 5
                if basis > 0:
                    v = min(v, vol_headroom(positions.get(fid, 0), "ask"),
                            vol_headroom(positions.get("ASML", 0), "bid"))
                    if v > 0:
                        e.insert_order(fid, price=fb.bids[0].price, volume=v, side="ask", order_type="ioc")
                        e.insert_order("ASML", price=sb.asks[0].price, volume=v, side="bid", order_type="ioc")
                else:
                    v = min(v, vol_headroom(positions.get(fid, 0), "bid"),
                            vol_headroom(positions.get("ASML", 0), "ask"))
                    if v > 0:
                        e.insert_order(fid, price=fb.asks[0].price, volume=v, side="bid", order_type="ioc")
                        e.insert_order("ASML", price=sb.bids[0].price, volume=v, side="ask", order_type="ioc")


# ---- Main ----

e = Exchange()
e.connect()

instruments = e.get_instruments()
log.info(f"connected, {len(instruments)} instruments")

# Discover
dual_pairs = [(iid, iid + "_DUAL") for iid in sorted(instruments) if iid + "_DUAL" in instruments]
log.info(f"dual pairs: {dual_pairs}")

ob5x_futures = sorted(
    [iid for iid in instruments if "OB5X" in iid and iid.endswith("_F")],
    key=lambda x: instruments[x].expiry,
)
primary_future = ob5x_futures[0] if ob5x_futures else None
log.info(f"OB5X futures: {ob5x_futures}, primary: {primary_future}")

asml_futures = sorted(
    [iid for iid in instruments if iid.startswith("ASML_") and iid.endswith("_F")],
    key=lambda x: instruments[x].expiry,
)

stock_options = {
    iid: inst for iid, inst in instruments.items()
    if inst.instrument_type == InstrumentType.STOCK_OPTION and inst.base_instrument_id == "ASML"
}
index_options = {
    iid: inst for iid, inst in instruments.items()
    if inst.instrument_type == InstrumentType.INDEX_OPTION
}
log.info(f"stock options: {list(stock_options.keys())}")
log.info(f"index options: {list(index_options.keys())}")

# Build put-call pairs for cross arb
opt_pairs = {}
for iid, inst in stock_options.items():
    key = (inst.base_instrument_id, inst.expiry, inst.strike)
    if key not in opt_pairs:
        opt_pairs[key] = {}
    opt_pairs[key][inst.option_kind] = iid

constituent_index: float | None = None
iteration = 0

while True:
    try:
        if not e.is_connected():
            log.warning("disconnected, reconnecting...")
            e = Exchange()
            e.connect()
            instruments = e.get_instruments()
            time.sleep(3)
            continue

        positions = e.get_positions()
        iteration += 1

        # Compute ASML mid (used by options + dual listing)
        asml_mid = get_mid(e, "ASML")

        # Compute constituent index every 3rd iteration (5 API calls)
        if iteration % 3 == 1:
            constituent_index = compute_index(e)

        # ---- Phase 1: Dual listing (fast, ~10 API calls per pair) ----
        run_dual_listing(e, dual_pairs, positions)
        time.sleep(0.5)

        # ---- Phase 2: ETF-Future quoting (~8 API calls) ----
        if primary_future:
            run_etf_future(e, primary_future, instruments, positions, constituent_index)
        time.sleep(0.5)

        # ---- Phase 3: Options MM (~4 calls per option, 12 options = ~48 calls over ~1.2s) ----
        if asml_mid is not None:
            run_options_mm(e, stock_options, index_options, asml_mid, constituent_index, positions)

        # Re-read positions after options quoting (fills may have happened)
        positions = e.get_positions()

        # ---- Phase 4: Delta hedge ----
        if asml_mid is not None:
            hedge_stock_options(e, stock_options, asml_mid, positions)
        if constituent_index is not None and primary_future:
            hedge_index_options(e, index_options, constituent_index, positions, primary_future)
        time.sleep(0.5)

        # ---- Phase 5: Cross-instrument arb (every 5th iteration) ----
        if iteration % 5 == 0:
            run_cross_arb(e, instruments, opt_pairs, asml_futures, positions)

        # Status
        if iteration % 10 == 0:
            pnl = e.get_pnl()
            active = {k: v for k, v in positions.items() if v != 0}
            log.info(f"[iter {iteration}] PnL={pnl:.2f}  {active}")

        time.sleep(2.0)

    except Exception as ex:
        log.error(f"Error: {ex}")
        time.sleep(3)
