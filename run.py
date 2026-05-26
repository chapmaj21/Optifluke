import time
import logging
from math import exp

from optibook.synchronous_client import Exchange
from optibook.common_types import InstrumentType, OptionKind

import sys
sys.path.append("/home/workspace/your_optiver_workspace")
from common.libs import calculate_current_time_to_date

from dual_listing.your_dual_listing_algo import run_dual_listing
from etf_future.your_etf_future_algo import run_etf_quoting
from options_market_making.your_options_mm_algo import quote_single_option, compute_stock_delta, compute_index_delta
from cross_instrument_arb.cross_arb import run_cross_arb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)-8s] %(message)s", datefmt="%H:%M:%S")
logging.getLogger("client").setLevel("ERROR")
log = logging.getLogger("run")

POSITION_LIMIT = 100
RATE = 0.03
SIGMA = 3.0
CONSTITUENTS = {"ASML": 908.06, "AAPL": 129.24, "SAP": 124.78, "TSLA": 2245.39, "NVDA": 953.21}
INDEX_DIVISOR = 1000.0
ETF_M = 0.25
OPTIONS_PER_ITER = 2
LOOP_SLEEP = 0.25
DELTA_HEDGE_THRESHOLD = 0.5


class RateLimiter:
    def __init__(self, rate: int = 20):
        self._rate = rate
        self._tokens = float(rate)
        self._max = float(rate)
        self._last = time.monotonic()

    def acquire(self, n: int = 1):
        while True:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._max, self._tokens + elapsed * self._rate)
            if self._tokens >= n:
                self._tokens -= n
                return
            wait = (n - self._tokens) / self._rate
            time.sleep(wait)


class Ex:
    def __init__(self):
        self._inner = Exchange()
        self._inner.connect()
        self._rl = RateLimiter(20)

    def reconnect(self):
        self._inner = Exchange()
        self._inner.connect()

    def is_connected(self):
        return self._inner.is_connected()

    def get_instruments(self):
        self._rl.acquire()
        return self._inner.get_instruments()

    def get_positions(self):
        self._rl.acquire()
        return self._inner.get_positions()

    def get_pnl(self):
        self._rl.acquire()
        return self._inner.get_pnl()

    def book(self, iid: str):
        self._rl.acquire()
        return self._inner.get_last_price_book(iid)

    def insert(self, iid: str, price: float, volume: int, side: str, otype: str):
        self._rl.acquire()
        return self._inner.insert_order(iid, price=price, volume=volume, side=side, order_type=otype)

    def cancel(self, iid: str):
        self._rl.acquire()
        self._inner.delete_orders(iid)


class Pos:
    def __init__(self, raw: dict):
        self._p = dict(raw)

    def get(self, iid: str) -> int:
        return self._p.get(iid, 0)

    def hr(self, iid: str, side: str) -> int:
        p = self.get(iid)
        return max(0, POSITION_LIMIT - p) if side == "bid" else max(0, POSITION_LIMIT + p)

    def fill(self, iid: str, vol: int, side: str):
        if side == "bid":
            self._p[iid] = self.get(iid) + vol
        else:
            self._p[iid] = self.get(iid) - vol

    def items(self):
        return self._p.items()


def _bv(b) -> bool:
    return b and b.bids and b.asks


def _bmid(b) -> float | None:
    return (b.bids[0].price + b.asks[0].price) / 2.0 if _bv(b) else None


def discover(e):
    insts = e.get_instruments()
    duals = [(iid, iid + "_DUAL") for iid in sorted(insts) if iid + "_DUAL" in insts]
    ob5x_futs = sorted(
        [i for i in insts if "OB5X" in i and i.endswith("_F")],
        key=lambda x: insts[x].expiry,
    )
    stock_futs: dict[str, list[str]] = {}
    for iid, inst in insts.items():
        if inst.instrument_type == InstrumentType.STOCK_FUTURE:
            stock_futs.setdefault(inst.base_instrument_id, []).append(iid)
    for k in stock_futs:
        stock_futs[k].sort(key=lambda x: insts[x].expiry)

    stock_opts: dict[str, dict] = {}
    for iid, inst in insts.items():
        if inst.instrument_type == InstrumentType.STOCK_OPTION:
            stock_opts.setdefault(inst.base_instrument_id, {})[iid] = inst

    idx_opts = {i: inst for i, inst in insts.items() if inst.instrument_type == InstrumentType.INDEX_OPTION}

    option_pairs: dict = {}
    for iid, inst in insts.items():
        if inst.instrument_type == InstrumentType.STOCK_OPTION:
            key = (inst.base_instrument_id, inst.expiry, inst.strike)
            option_pairs.setdefault(key, {})[inst.option_kind] = iid

    all_options: list[tuple[str, object, str]] = []
    for base, opts in stock_opts.items():
        for oid, inst in opts.items():
            all_options.append((oid, inst, base))
    for oid, inst in idx_opts.items():
        all_options.append((oid, inst, "OB5X"))

    primary_fut = ob5x_futs[0] if ob5x_futs else None

    log.info(f"{len(insts)} instruments: {len(duals)} dual pairs, {len(ob5x_futs)} OB5X futs, "
             f"stock_futs={list(stock_futs.keys())}, stock_opts={list(stock_opts.keys())}, "
             f"{len(idx_opts)} idx opts, {len(all_options)} total options")

    return insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts, option_pairs, all_options, primary_fut


def cancel_all(e, insts):
    for iid in insts:
        try:
            e.cancel(iid)
        except Exception:
            time.sleep(1)
            try:
                e.cancel(iid)
            except Exception:
                pass
    time.sleep(0.5)


def compute_constituent_index(e) -> float | None:
    total = 0.0
    for sid, w in CONSTITUENTS.items():
        m = _bmid(e.book(sid))
        if m is None:
            return None
        total += w * m
    return total / INDEX_DIVISOR


def hedge_stock(ex, underlying: str, delta: float, pos):
    if abs(delta) <= DELTA_HEDGE_THRESHOLD:
        return
    b = ex.book(underlying)
    if not _bv(b):
        return
    if delta > DELTA_HEDGE_THRESHOLD:
        lots = min(round(delta), pos.hr(underlying, "ask"), POSITION_LIMIT)
        if lots > 0:
            ex.cancel(underlying)
            ex.insert(underlying, b.bids[0].price, lots, "ask", "ioc")
            pos.fill(underlying, lots, "ask")
    else:
        lots = min(round(abs(delta)), pos.hr(underlying, "bid"), POSITION_LIMIT)
        if lots > 0:
            ex.cancel(underlying)
            ex.insert(underlying, b.asks[0].price, lots, "bid", "ioc")
            pos.fill(underlying, lots, "bid")


def hedge_all_deltas(ex, insts, stock_opts, stock_futs, idx_opts, ob5x_futs, primary_fut, pos):
    for underlying, opts in stock_opts.items():
        u_mid = _bmid(ex.book(underlying))
        if u_mid is None:
            continue
        futs = stock_futs.get(underlying, [])
        delta = compute_stock_delta(pos, underlying, opts, futs, u_mid)
        hedge_stock(ex, underlying, delta, pos)

    if primary_fut and ob5x_futs:
        pfb = ex.book(primary_fut)
        if _bv(pfb):
            tau = calculate_current_time_to_date(insts[primary_fut].expiry)
            if tau > 0:
                idx_val = _bmid(pfb) / exp(RATE * tau)
                delta = compute_index_delta(pos, idx_opts, ob5x_futs, ETF_M, idx_val)
                if abs(delta) > DELTA_HEDGE_THRESHOLD:
                    if delta > DELTA_HEDGE_THRESHOLD:
                        lots = min(round(delta), pos.hr(primary_fut, "ask"), POSITION_LIMIT)
                        if lots > 0:
                            ex.cancel(primary_fut)
                            ex.insert(primary_fut, pfb.bids[0].price, lots, "ask", "ioc")
                            pos.fill(primary_fut, lots, "ask")
                    else:
                        lots = min(round(abs(delta)), pos.hr(primary_fut, "bid"), POSITION_LIMIT)
                        if lots > 0:
                            ex.cancel(primary_fut)
                            ex.insert(primary_fut, pfb.asks[0].price, lots, "bid", "ioc")
                            pos.fill(primary_fut, lots, "bid")


e = Ex()
insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts, option_pairs, all_options, primary_fut = discover(e)

const_idx: float | None = None
opt_cursor = 0
dual_cursor = 0
arb_cursor = 0
iteration = 0

cancel_all(e, insts)

log.info(f"primary_fut={primary_fut}, {len(all_options)} options, {len(duals)} dual pairs")

while True:
    try:
        if not e.is_connected():
            log.warning("disconnected, reconnecting...")
            e.reconnect()
            insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts, option_pairs, all_options, primary_fut = discover(e)
            opt_cursor = 0
            dual_cursor = 0
            arb_cursor = 0
            cancel_all(e, insts)
            time.sleep(2)
            continue

        pos = Pos(e.get_positions())
        iteration += 1

        if iteration % 5 == 1:
            const_idx = compute_constituent_index(e)

        tau = 0.0
        fut_idx = None
        if primary_fut:
            pfb = e.book(primary_fut)
            if _bv(pfb):
                tau = calculate_current_time_to_date(insts[primary_fut].expiry)
                if tau > 0:
                    fut_idx = _bmid(pfb) / exp(RATE * tau)

        pricing_idx = fut_idx or const_idx

        if duals:
            pair = duals[dual_cursor % len(duals)]
            run_dual_listing(e, pair[0], pair[1], pos, insts)
            dual_cursor += 1

        run_etf_quoting(e, primary_fut, insts, pos, const_idx, tau)

        if all_options:
            for i in range(OPTIONS_PER_ITER):
                idx = (opt_cursor + i) % len(all_options)
                oid, opt, base = all_options[idx]
                if base == "OB5X":
                    u_mid = pricing_idx
                else:
                    u_mid = _bmid(e.book(base)) if base in insts else None
                if u_mid is not None:
                    quote_single_option(e, oid, opt, u_mid, pos, insts)
            opt_cursor = (opt_cursor + OPTIONS_PER_ITER) % max(1, len(all_options))

        if iteration % 4 == 0:
            run_cross_arb(e, ob5x_futs, stock_futs, option_pairs, insts, pos, arb_cursor)
            arb_cursor += 1

        hedge_all_deltas(e, insts, stock_opts, stock_futs, idx_opts, ob5x_futs, primary_fut, pos)

        if iteration % 20 == 0:
            pnl = e.get_pnl()
            active = {k: v for k, v in pos.items() if v != 0}
            log.info(f"[iter {iteration}] PnL={pnl:.2f}  {active}")

        time.sleep(LOOP_SLEEP)

    except Exception as ex:
        log.error(f"error: {ex}")
        time.sleep(2)
