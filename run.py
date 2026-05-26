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
from options_market_making.your_options_mm_algo import quote_single_option, compute_stock_delta, compute_index_delta, option_delta
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
OPTIONS_PER_ITER = 4
LOOP_SLEEP = 0.25
DELTA_HEDGE_THRESHOLD = 0.5
DIAG_INTERVAL = 20
QUOTE_INDEX_OPTIONS = True
UNWIND_INDEX_OPTIONS = False
INDEX_OPTION_UNWIND_PER_ITER = 1
INDEX_OPTION_UNWIND_LOT = 8
DUAL_NET_HEDGE_THRESHOLD = 2
TOURNAMENT_OPTION_CREDIT_MULT = 0.82
ASML_DELTA_SOFT = 70.0
ASML_DELTA_HARD = 88.0
INDEX_DELTA_SOFT = 55.0
INDEX_DELTA_HARD = 75.0
TOURNAMENT_OPTION_VOLUME = 12
RISK_REDUCING_OPTION_VOLUME = 14
NEAR_LIMIT_OPTION_VOLUME = 4


class RateLimiter:
    def __init__(self, rate: int = 18):
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
        self._rl = RateLimiter(18)

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
        resp = self._inner.insert_order(iid, price=price, volume=volume, side=side, order_type=otype)
        if not getattr(resp, "success", False):
            log.warning(f"insert failed {iid} {side} {volume}@{price:.2f} {otype}: {getattr(resp, 'error_reason', None)}")
        return resp

    def poll_trades(self, iid: str):
        self._rl.acquire()
        return self._inner.poll_new_trades(iid)

    def ioc(self, iid: str, price: float, volume: int, side: str) -> int:
        if volume <= 0:
            return 0

        resp = self.insert(iid, price, volume, side, "ioc")
        if not getattr(resp, "success", False):
            return 0

        order_id = getattr(resp, "order_id", None)
        if order_id is None:
            return 0

        filled = 0
        # IOC fills should be available immediately, but one short retry avoids
        # treating a delayed trade callback as a miss.
        for attempt in range(2):
            trades = self.poll_trades(iid)
            filled += sum(t.volume for t in trades if getattr(t, "order_id", None) == order_id)
            if attempt == 0:
                time.sleep(0.02)
        return filled

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

    def replace(self, raw: dict):
        self._p = dict(raw)

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

    if QUOTE_INDEX_OPTIONS:
        for oid, inst in idx_opts.items():
            all_options.append((oid, inst, "OB5X"))

    all_options.sort(key=lambda x: (x[2], x[1].expiry, x[1].strike, str(x[1].option_kind), x[0]))

    primary_fut = ob5x_futs[0] if ob5x_futs else None

    log.info(f"{len(insts)} instruments: {len(duals)} dual pairs, {len(ob5x_futs)} OB5X futs, "
             f"stock_futs={list(stock_futs.keys())}, stock_opts={list(stock_opts.keys())}, "
             f"{len(idx_opts)} idx opts, {len(all_options)} quoted options")

    return insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts, option_pairs, all_options, primary_fut


def compute_constituent_index(e) -> float | None:
    total = 0.0
    for sid, w in CONSTITUENTS.items():
        m = _bmid(e.book(sid))
        if m is None:
            return None
        total += w * m
    return total / INDEX_DIVISOR


def cancel_all_orders(ex, insts):
    for iid in sorted(insts):
        ex.cancel(iid)


def _future_delta_unit(insts, fid: str) -> float:
    tau = calculate_current_time_to_date(insts[fid].expiry)
    return exp(RATE * tau) if tau > 0 else 1.0


def _option_quote_controls(base_delta: float, opt_delta: float, soft_limit: float, hard_limit: float):
    volume = TOURNAMENT_OPTION_VOLUME
    if abs(base_delta) > soft_limit:
        volume = NEAR_LIMIT_OPTION_VOLUME

    allow_bid = abs(base_delta + opt_delta * volume) <= hard_limit
    allow_ask = abs(base_delta - opt_delta * volume) <= hard_limit

    if base_delta > soft_limit:
        # Positive portfolio delta: only keep sides that can reduce it.
        if opt_delta > 0:
            allow_bid = False
        elif opt_delta < 0:
            allow_ask = False
    elif base_delta < -soft_limit:
        # Negative portfolio delta: only keep sides that can reduce it.
        if opt_delta > 0:
            allow_ask = False
        elif opt_delta < 0:
            allow_bid = False

    if (base_delta > soft_limit and ((opt_delta > 0 and allow_ask) or (opt_delta < 0 and allow_bid))) or (
        base_delta < -soft_limit and ((opt_delta > 0 and allow_bid) or (opt_delta < 0 and allow_ask))
    ):
        volume = RISK_REDUCING_OPTION_VOLUME

    return allow_bid, allow_ask, volume


def _reserve_worst_option_delta(shadow_delta: float, opt_delta: float, allow_bid: bool, allow_ask: bool, volume: int) -> float:
    candidates = [shadow_delta]
    if allow_bid:
        candidates.append(shadow_delta + opt_delta * volume)
    if allow_ask:
        candidates.append(shadow_delta - opt_delta * volume)
    return max(candidates, key=lambda x: abs(x))


def run_diagnostics(ex, pos, insts, stock_opts, stock_futs, idx_opts, ob5x_futs, primary_fut, duals, prev_pnl, iteration):
    pnl = ex.get_pnl()
    dpnl = pnl - prev_pnl if prev_pnl is not None else 0.0

    lines = [f"=== DIAG iter={iteration} PnL={pnl:.2f} (d={dpnl:+.2f}) ==="]

    active = {k: v for k, v in pos.items() if v != 0}
    if active:
        pos_parts = [f"{k}={v:+d}" for k, v in sorted(active.items())]
        lines.append(f"POS: {' '.join(pos_parts)}")
    else:
        lines.append("POS: flat")

    for underlying, opts in stock_opts.items():
        u_mid = _bmid(ex.book(underlying))
        if u_mid is None:
            continue
        futs = stock_futs.get(underlying, [])
        delta = compute_stock_delta(pos, underlying, opts, futs, u_mid, insts)
        if abs(delta) > 0.1:
            lines.append(f"DELTA {underlying}: {delta:+.1f} (stock={pos.get(underlying):+d} dual={pos.get(underlying + '_DUAL'):+d} futs={sum(pos.get(f) for f in futs):+d})")

    if primary_fut and ob5x_futs:
        pfb = ex.book(primary_fut)
        if _bv(pfb):
            tau = calculate_current_time_to_date(insts[primary_fut].expiry)
            if tau > 0:
                idx_val = _bmid(pfb) / exp(RATE * tau)
                idx_delta = compute_index_delta(pos, idx_opts, ob5x_futs, ETF_M, idx_val, insts)
                etf_pos = pos.get("OB5X_ETF")
                fut_pos = sum(pos.get(f) for f in ob5x_futs)
                lines.append(f"DELTA OB5X: {idx_delta:+.1f} (etf={etf_pos:+d}*{ETF_M}={ETF_M*etf_pos:+.1f} futs={fut_pos:+d})")

    for liquid, dual in duals:
        lp, dp = pos.get(liquid), pos.get(dual)
        if lp != 0 or dp != 0:
            lines.append(f"DUAL {liquid}: liq={lp:+d} dual={dp:+d} net={lp+dp:+d}")

    opt_positions = []
    for oid in sorted(insts):
        p = pos.get(oid)
        if p != 0 and (insts[oid].instrument_type == InstrumentType.STOCK_OPTION or insts[oid].instrument_type == InstrumentType.INDEX_OPTION):
            opt_positions.append(f"{oid}={p:+d}")
    if opt_positions:
        lines.append(f"OPTS: {' '.join(opt_positions)}")

    log.info("\n".join(lines))
    return pnl


def unwind_index_options(ex, idx_opts, pos, cursor: int, max_options: int = INDEX_OPTION_UNWIND_PER_ITER) -> int:
    ids = sorted(idx_opts)
    if not ids:
        return cursor

    for i in range(min(max_options, len(ids))):
        oid = ids[(cursor + i) % len(ids)]
        p = pos.get(oid)
        if p == 0:
            continue
        ob = ex.book(oid)
        if not _bv(ob):
            continue
        if p > 0:
            sell_vol = min(p, INDEX_OPTION_UNWIND_LOT)
            ex.cancel(oid)
            filled = ex.ioc(oid, ob.bids[0].price, sell_vol, "ask")
            pos.fill(oid, filled, "ask")
        elif p < 0:
            buy_vol = min(abs(p), INDEX_OPTION_UNWIND_LOT)
            ex.cancel(oid)
            filled = ex.ioc(oid, ob.asks[0].price, buy_vol, "bid")
            pos.fill(oid, filled, "bid")

    return (cursor + max_options) % len(ids)


def hedge_unhandled_dual_nets(ex, duals, stock_opts, stock_futs, pos):
    for liquid, dual in duals:
        if liquid in stock_opts or stock_futs.get(liquid):
            continue

        net = pos.get(liquid) + pos.get(dual)
        if abs(net) <= DUAL_NET_HEDGE_THRESHOLD:
            continue

        book = ex.book(liquid)
        if not _bv(book):
            continue

        if net > 0:
            vol = min(abs(net), book.bids[0].volume, pos.hr(liquid, "ask"))
            if vol > 0:
                ex.cancel(liquid)
                filled = ex.ioc(liquid, book.bids[0].price, vol, "ask")
                pos.fill(liquid, filled, "ask")
        else:
            vol = min(abs(net), book.asks[0].volume, pos.hr(liquid, "bid"))
            if vol > 0:
                ex.cancel(liquid)
                filled = ex.ioc(liquid, book.asks[0].price, vol, "bid")
                pos.fill(liquid, filled, "bid")


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
            filled = ex.ioc(underlying, b.bids[0].price, lots, "ask")
            pos.fill(underlying, filled, "ask")
    else:
        lots = min(round(abs(delta)), pos.hr(underlying, "bid"), POSITION_LIMIT)
        if lots > 0:
            ex.cancel(underlying)
            filled = ex.ioc(underlying, b.asks[0].price, lots, "bid")
            pos.fill(underlying, filled, "bid")


def hedge_stock_complex(ex, underlying: str, delta: float, stock_futs: list[str], insts: dict, pos):
    remaining = delta
    if abs(remaining) <= DELTA_HEDGE_THRESHOLD:
        return

    hedge_instruments: list[tuple[str, float]] = [(underlying, 1.0)]
    for fid in stock_futs:
        if fid in insts:
            hedge_instruments.append((fid, _future_delta_unit(insts, fid)))

    for iid, unit in hedge_instruments:
        if abs(remaining) <= DELTA_HEDGE_THRESHOLD:
            return

        book = ex.book(iid)
        if not _bv(book):
            continue

        if remaining > 0:
            lots = min(round(abs(remaining) / unit), book.bids[0].volume, pos.hr(iid, "ask"), POSITION_LIMIT)
            if lots <= 0:
                continue
            ex.cancel(iid)
            filled = ex.ioc(iid, book.bids[0].price, lots, "ask")
            pos.fill(iid, filled, "ask")
            remaining -= filled * unit
        else:
            lots = min(round(abs(remaining) / unit), book.asks[0].volume, pos.hr(iid, "bid"), POSITION_LIMIT)
            if lots <= 0:
                continue
            ex.cancel(iid)
            filled = ex.ioc(iid, book.asks[0].price, lots, "bid")
            pos.fill(iid, filled, "bid")
            remaining += filled * unit


def hedge_index_complex(ex, insts, ob5x_futs, primary_fut, delta: float, pos):
    remaining = delta
    if abs(remaining) <= DELTA_HEDGE_THRESHOLD:
        return

    hedge_ids = []
    if primary_fut:
        hedge_ids.append(primary_fut)
    hedge_ids.extend(fid for fid in ob5x_futs if fid != primary_fut)

    for fid in hedge_ids:
        if abs(remaining) <= DELTA_HEDGE_THRESHOLD:
            return
        if fid not in insts:
            continue

        book = ex.book(fid)
        if not _bv(book):
            continue

        unit = _future_delta_unit(insts, fid)
        if remaining > 0:
            lots = min(round(abs(remaining) / unit), book.bids[0].volume, pos.hr(fid, "ask"), POSITION_LIMIT)
            if lots <= 0:
                continue
            ex.cancel(fid)
            filled = ex.ioc(fid, book.bids[0].price, lots, "ask")
            pos.fill(fid, filled, "ask")
            remaining -= filled * unit
        else:
            lots = min(round(abs(remaining) / unit), book.asks[0].volume, pos.hr(fid, "bid"), POSITION_LIMIT)
            if lots <= 0:
                continue
            ex.cancel(fid)
            filled = ex.ioc(fid, book.asks[0].price, lots, "bid")
            pos.fill(fid, filled, "bid")
            remaining += filled * unit


def hedge_all_deltas(ex, insts, stock_opts, stock_futs, idx_opts, ob5x_futs, primary_fut, pos):
    # re-fetch real positions before hedging to avoid stale data
    pos.replace(ex.get_positions())

    for underlying, opts in stock_opts.items():
        u_mid = _bmid(ex.book(underlying))
        if u_mid is None:
            continue
        futs = stock_futs.get(underlying, [])
        delta = compute_stock_delta(pos, underlying, opts, futs, u_mid, insts)
        hedge_stock_complex(ex, underlying, delta, futs, insts, pos)

    if primary_fut and ob5x_futs:
        pfb = ex.book(primary_fut)
        if _bv(pfb):
            tau = calculate_current_time_to_date(insts[primary_fut].expiry)
            if tau > 0:
                idx_val = _bmid(pfb) / exp(RATE * tau)
                delta = compute_index_delta(pos, idx_opts, ob5x_futs, ETF_M, idx_val, insts)
                if abs(delta) > DELTA_HEDGE_THRESHOLD:
                    hedge_index_complex(ex, insts, ob5x_futs, primary_fut, delta, pos)


e = Ex()
insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts, option_pairs, all_options, primary_fut = discover(e)
cancel_all_orders(e, insts)

const_idx: float | None = None
prev_pnl: float | None = None
opt_cursor = 0
dual_cursor = 0
arb_cursor = 0
idx_unwind_cursor = 0
iteration = 0

log.info(f"primary_fut={primary_fut}, {len(all_options)} options, {len(duals)} dual pairs")

while True:
    try:
        if not e.is_connected():
            log.warning("disconnected, reconnecting...")
            e.reconnect()
            insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts, option_pairs, all_options, primary_fut = discover(e)
            cancel_all_orders(e, insts)
            opt_cursor = 0
            dual_cursor = 0
            arb_cursor = 0
            idx_unwind_cursor = 0
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
        pricing_idx_bid = None
        pricing_idx_ask = None
        if primary_fut and _bv(pfb) and tau > 0:
            disc = exp(RATE * tau)
            pricing_idx_bid = pfb.bids[0].price / disc
            pricing_idx_ask = pfb.asks[0].price / disc
        if const_idx is not None:
            pricing_idx_bid = const_idx if pricing_idx_bid is None else min(pricing_idx_bid, const_idx)
            pricing_idx_ask = const_idx if pricing_idx_ask is None else max(pricing_idx_ask, const_idx)

        if duals:
            pair = duals[dual_cursor % len(duals)]
            run_dual_listing(e, pair[0], pair[1], pos, insts)
            dual_cursor += 1

        run_etf_quoting(e, primary_fut, insts, pos, const_idx, tau)

        if all_options:
            underlying_quotes: dict[str, tuple[float, float, float]] = {}
            shadow_deltas: dict[str, float] = {}
            for i in range(OPTIONS_PER_ITER):
                idx = (opt_cursor + i) % len(all_options)
                oid, opt, base = all_options[idx]
                if base == "OB5X":
                    if pricing_idx is not None:
                        underlying_quotes[base] = (
                            pricing_idx_bid if pricing_idx_bid is not None else pricing_idx,
                            pricing_idx_ask if pricing_idx_ask is not None else pricing_idx,
                            pricing_idx,
                        )
                else:
                    if base not in underlying_quotes and base in insts:
                        ub = e.book(base)
                        if _bv(ub):
                            underlying_quotes[base] = (ub.bids[0].price, ub.asks[0].price, _bmid(ub))
                quote = underlying_quotes.get(base)
                if quote is not None:
                    u_bid, u_ask, u_mid = quote
                    if base not in shadow_deltas:
                        if base == "OB5X":
                            shadow_deltas[base] = compute_index_delta(pos, idx_opts, ob5x_futs, ETF_M, u_mid, insts)
                        else:
                            shadow_deltas[base] = compute_stock_delta(
                                pos,
                                base,
                                stock_opts.get(base, {}),
                                stock_futs.get(base, []),
                                u_mid,
                                insts,
                            )

                    soft = INDEX_DELTA_SOFT if base == "OB5X" else ASML_DELTA_SOFT
                    hard = INDEX_DELTA_HARD if base == "OB5X" else ASML_DELTA_HARD
                    opt_d = option_delta(opt, u_mid)
                    allow_bid, allow_ask, quote_volume = _option_quote_controls(shadow_deltas[base], opt_d, soft, hard)

                    quote_single_option(
                        e,
                        oid,
                        opt,
                        u_mid,
                        pos,
                        insts,
                        u_bid,
                        u_ask,
                        allow_bid=allow_bid,
                        allow_ask=allow_ask,
                        volume_override=quote_volume,
                        credit_mult=TOURNAMENT_OPTION_CREDIT_MULT,
                    )
                    shadow_deltas[base] = _reserve_worst_option_delta(
                        shadow_deltas[base],
                        opt_d,
                        allow_bid,
                        allow_ask,
                        quote_volume,
                    )
            opt_cursor = (opt_cursor + OPTIONS_PER_ITER) % max(1, len(all_options))

        if UNWIND_INDEX_OPTIONS:
            idx_unwind_cursor = unwind_index_options(e, idx_opts, pos, idx_unwind_cursor)

        if iteration % 4 == 0:
            run_cross_arb(e, ob5x_futs, stock_futs, option_pairs, insts, pos, arb_cursor)
            arb_cursor += 1

        hedge_all_deltas(e, insts, stock_opts, stock_futs, idx_opts, ob5x_futs, primary_fut, pos)
        hedge_unhandled_dual_nets(e, duals, stock_opts, stock_futs, pos)

        if iteration % DIAG_INTERVAL == 0:
            prev_pnl = run_diagnostics(e, pos, insts, stock_opts, stock_futs, idx_opts, ob5x_futs, primary_fut, duals, prev_pnl, iteration)

        time.sleep(LOOP_SLEEP)

    except Exception as ex:
        log.error(f"error: {ex}")
        time.sleep(2)
