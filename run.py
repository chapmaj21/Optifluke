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

POSITION_LIMIT = 100
RATE = 0.03
SIGMA = 3.0
CONSTITUENTS = {"ASML": 908.06, "AAPL": 129.24, "SAP": 124.78, "TSLA": 2245.39, "NVDA": 953.21}
INDEX_DIVISOR = 1000.0
ETF_M = 0.25
ETF_C = 2.50

DUAL_CREDIT = 0.02
DUAL_VOLUME = 40
ETF_CREDIT = 0.02
ETF_VOLUME = 40
OPT_VOLUME = 15
OPT_BASE_CREDIT = 0.05
OPT_VEGA_SCALE = 0.02
OPT_SPREAD_SCALE = 0.1
OPT_MIN_FLOOR = 0.10
OPT_MIN_PCT = 0.04
BASIS_THRESHOLD = 0.10
CALENDAR_THRESHOLD = 0.05
OPTIONS_PER_ITER = 3


class RateLimiter:
    def __init__(self, rate: int = 18):
        self._interval = 1.0 / rate
        self._last = 0.0

    def acquire(self):
        now = time.monotonic()
        gap = self._interval - (now - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


class Ex:
    def __init__(self, rate: int = 18):
        self._inner = Exchange()
        self._inner.connect()
        self._rl = RateLimiter(rate)
        self._books: dict = {}

    def _r(self):
        self._rl.acquire()

    def clear_cache(self):
        self._books.clear()

    def reconnect(self):
        self._inner = Exchange()
        self._inner.connect()

    def is_connected(self):
        return self._inner.is_connected()

    def get_instruments(self):
        self._r()
        return self._inner.get_instruments()

    def get_positions(self):
        self._r()
        return self._inner.get_positions()

    def get_pnl(self):
        self._r()
        return self._inner.get_pnl()

    def book(self, iid: str):
        if iid not in self._books:
            self._r()
            self._books[iid] = self._inner.get_last_price_book(iid)
        return self._books[iid]

    def insert(self, iid: str, price: float, volume: int, side: str, otype: str):
        self._r()
        return self._inner.insert_order(iid, price=price, volume=volume, side=side, order_type=otype)

    def cancel(self, iid: str):
        self._r()
        return self._inner.delete_orders(iid)


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


def bok(b) -> bool:
    return b and b.bids and b.asks


def bmid(b) -> float | None:
    return (b.bids[0].price + b.asks[0].price) / 2.0 if bok(b) else None


def td(price: float, tick: float) -> float:
    return floor(price / tick) * tick


def tu(price: float, tick: float) -> float:
    return ceil(price / tick) * tick


def bsv(S, K, T, r, s, kind):
    return (call_value if kind == OptionKind.CALL else put_value)(S=S, K=K, T=T, r=r, sigma=s)


def bsd(S, K, T, r, s, kind):
    return (call_delta if kind == OptionKind.CALL else put_delta)(S=S, K=K, T=T, r=r, sigma=s)


def bsve(S, K, T, r, s, kind):
    return (call_vega if kind == OptionKind.CALL else put_vega)(S=S, K=K, T=T, r=r, sigma=s)


def opt_credit(theo: float, vega: float, spread: float) -> float:
    c = OPT_BASE_CREDIT + OPT_VEGA_SCALE * abs(vega) + OPT_SPREAD_SCALE * spread
    c = max(c, OPT_MIN_PCT * theo)
    return max(OPT_MIN_FLOOR, c)


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

    log.info(f"{len(insts)} instruments: {len(duals)} dual pairs, {len(ob5x_futs)} OB5X futs, "
             f"stock_futs={list(stock_futs.keys())}, stock_opts={list(stock_opts.keys())}, "
             f"{len(idx_opts)} idx opts")

    return insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts


def clear_positions(e):
    log.info("clearing all orders and flattening positions...")
    instruments = e.get_instruments()
    for iid in instruments:
        e.cancel(iid)

    time.sleep(1)
    positions = e.get_positions()
    for iid, p in positions.items():
        if p == 0:
            continue
        b = e.book(iid)
        if not bok(b):
            continue
        if p > 0:
            e.insert(iid, b.bids[0].price, abs(p), "ask", "ioc")
        else:
            e.insert(iid, b.asks[0].price, abs(p), "bid", "ioc")

    time.sleep(1)
    e.clear_cache()
    positions = e.get_positions()
    pnl = e.get_pnl()
    remaining = {k: v for k, v in positions.items() if v != 0}
    log.info(f"after clearing: PnL={pnl:.2f}  remaining={remaining or 'flat'}")


def run_dual(e, duals, pos, insts):
    for liquid, dual in duals:
        lb = e.book(liquid)
        db = e.book(dual)
        if not (bok(lb) and bok(db)):
            continue

        tick = insts[dual].tick_size
        lbid, lask = lb.bids[0].price, lb.asks[0].price
        dbid, dask = db.bids[0].price, db.asks[0].price

        e.cancel(dual)

        if dask < lbid:
            v = min(db.asks[0].volume, DUAL_VOLUME, pos.hr(dual, "bid"), pos.hr(liquid, "ask"))
            if v > 0:
                e.insert(dual, dask, v, "bid", "ioc")
                pos.fill(dual, v, "bid")
                e.insert(liquid, lbid, v, "ask", "ioc")
                pos.fill(liquid, v, "ask")

        if dbid > lask:
            v = min(db.bids[0].volume, DUAL_VOLUME, pos.hr(dual, "ask"), pos.hr(liquid, "bid"))
            if v > 0:
                e.insert(dual, dbid, v, "ask", "ioc")
                pos.fill(dual, v, "ask")
                e.insert(liquid, lask, v, "bid", "ioc")
                pos.fill(liquid, v, "bid")

        dp = pos.get(dual)
        bv = min(DUAL_VOLUME, pos.hr(dual, "bid"))
        av = min(DUAL_VOLUME, pos.hr(dual, "ask"))
        if dp > 10:
            bv = max(0, bv - dp // 2)
        elif dp < -10:
            av = max(0, av + dp // 2)

        ob = td(lbid - DUAL_CREDIT, tick)
        oa = tu(lask + DUAL_CREDIT, tick)
        if bv > 0 and ob > 0:
            e.insert(dual, ob, bv, "bid", "limit")
        if av > 0 and oa > 0:
            e.insert(dual, oa, av, "ask", "limit")

        net = pos.get(dual) + pos.get(liquid)
        if abs(net) > 1:
            e.cancel(liquid)
            lb2 = e.book(liquid)
            if bok(lb2):
                if net > 0:
                    hv = min(abs(net), pos.hr(liquid, "ask"))
                    if hv > 0:
                        e.insert(liquid, lb2.bids[0].price, hv, "ask", "ioc")
                        pos.fill(liquid, hv, "ask")
                else:
                    hv = min(abs(net), pos.hr(liquid, "bid"))
                    if hv > 0:
                        e.insert(liquid, lb2.asks[0].price, hv, "bid", "ioc")
                        pos.fill(liquid, hv, "bid")


def run_etf(e, primary_fut, insts, pos, const_idx):
    if not primary_fut or "OB5X_ETF" not in insts:
        return

    fb = e.book(primary_fut)
    if not bok(fb):
        return

    tau = calculate_current_time_to_date(insts[primary_fut].expiry)
    if tau <= 0:
        tau = 1e-6

    fbid, fask = fb.bids[0].price, fb.asks[0].price
    tick = insts["OB5X_ETF"].tick_size

    fair_bid = ETF_C + ETF_M * (fbid / exp(RATE * tau))
    fair_ask = ETF_C + ETF_M * (fask / exp(RATE * tau))

    if const_idx is not None:
        from_const = ETF_C + ETF_M * const_idx
        fair_bid = min(fair_bid, from_const)
        fair_ask = max(fair_ask, from_const)

    bp = td(fair_bid - ETF_CREDIT, tick)
    ap = tu(fair_ask + ETF_CREDIT, tick)
    if bp <= 0 or ap <= 0 or bp >= ap:
        return

    ep = pos.get("OB5X_ETF")
    bv = min(ETF_VOLUME, pos.hr("OB5X_ETF", "bid"))
    av = min(ETF_VOLUME, pos.hr("OB5X_ETF", "ask"))
    if ep > 10:
        bv = max(0, bv - ep // 2)
    elif ep < -10:
        av = max(0, av + ep // 2)

    e.cancel("OB5X_ETF")
    if bv > 0:
        e.insert("OB5X_ETF", bp, bv, "bid", "limit")
    if av > 0:
        e.insert("OB5X_ETF", ap, av, "ask", "limit")

    fp = pos.get(primary_fut)
    target = -round(ETF_M * ep)
    hedge = target - fp
    if hedge != 0:
        side = "bid" if hedge > 0 else "ask"
        price = fask if hedge > 0 else fbid
        hv = min(abs(hedge), pos.hr(primary_fut, side))
        if hv > 0:
            e.insert(primary_fut, price, hv, side, "ioc")
            pos.fill(primary_fut, hv, side)


def quote_option(e, oid, opt, underlying_mid, pos, insts):
    T = calculate_current_time_to_date(opt.expiry)
    if T <= 0:
        return

    tick = insts[oid].tick_size if oid in insts else 0.10
    theo = bsv(underlying_mid, opt.strike, T, RATE, SIGMA, opt.option_kind)
    vega = bsve(underlying_mid, opt.strike, T, RATE, SIGMA, opt.option_kind)

    ob = e.book(oid)
    spread = (ob.asks[0].price - ob.bids[0].price) if bok(ob) else 0.0

    credit = opt_credit(theo, vega, spread)

    e.cancel(oid)
    bp = td(theo - credit, tick)
    ap = tu(theo + credit, tick)
    bv = min(OPT_VOLUME, pos.hr(oid, "bid"))
    av = min(OPT_VOLUME, pos.hr(oid, "ask"))

    if bv > 0 and bp > 0:
        e.insert(oid, bp, bv, "bid", "limit")
    if av > 0 and ap > 0:
        e.insert(oid, ap, av, "ask", "limit")


def run_futures_arb(e, ob5x_futs, stock_futs, insts, pos):
    for i in range(len(ob5x_futs)):
        for j in range(i + 1, len(ob5x_futs)):
            near, far = ob5x_futs[i], ob5x_futs[j]
            nb, fb = e.book(near), e.book(far)
            if not (bok(nb) and bok(fb)):
                continue
            tn = calculate_current_time_to_date(insts[near].expiry)
            tf = calculate_current_time_to_date(insts[far].expiry)
            if tn <= 0 or tf <= 0:
                continue
            fair_far = bmid(nb) * exp(RATE * (tf - tn))
            spread = bmid(fb) - fair_far

            if spread > CALENDAR_THRESHOLD:
                v = min(5, pos.hr(far, "ask"), pos.hr(near, "bid"))
                if v > 0:
                    e.insert(far, fb.bids[0].price, v, "ask", "ioc")
                    pos.fill(far, v, "ask")
                    e.insert(near, nb.asks[0].price, v, "bid", "ioc")
                    pos.fill(near, v, "bid")
            elif spread < -CALENDAR_THRESHOLD:
                v = min(5, pos.hr(far, "bid"), pos.hr(near, "ask"))
                if v > 0:
                    e.insert(far, fb.asks[0].price, v, "bid", "ioc")
                    pos.fill(far, v, "bid")
                    e.insert(near, nb.bids[0].price, v, "ask", "ioc")
                    pos.fill(near, v, "ask")

    for stock, futs in stock_futs.items():
        sb = e.book(stock)
        if not bok(sb):
            continue
        s_mid = bmid(sb)
        for fid in futs:
            if fid not in insts:
                continue
            fbook = e.book(fid)
            if not bok(fbook):
                continue
            tau = calculate_current_time_to_date(insts[fid].expiry)
            if tau <= 0:
                continue
            fair = s_mid * exp(RATE * tau)
            basis = bmid(fbook) - fair

            if basis > BASIS_THRESHOLD:
                v = min(5, pos.hr(fid, "ask"), pos.hr(stock, "bid"))
                if v > 0:
                    e.insert(fid, fbook.bids[0].price, v, "ask", "ioc")
                    pos.fill(fid, v, "ask")
                    e.insert(stock, sb.asks[0].price, v, "bid", "ioc")
                    pos.fill(stock, v, "bid")
            elif basis < -BASIS_THRESHOLD:
                v = min(5, pos.hr(fid, "bid"), pos.hr(stock, "ask"))
                if v > 0:
                    e.insert(fid, fbook.asks[0].price, v, "bid", "ioc")
                    pos.fill(fid, v, "bid")
                    e.insert(stock, sb.bids[0].price, v, "ask", "ioc")
                    pos.fill(stock, v, "ask")


def hedge_delta(e, pos, insts, stock_opts, idx_opts, ob5x_futs, stock_futs):
    asml_book = e.book("ASML")
    asml_mid = bmid(asml_book)
    if asml_mid is not None:
        delta = float(pos.get("ASML"))

        for oid, opt in stock_opts.get("ASML", {}).items():
            p = pos.get(oid)
            if p == 0:
                continue
            T = calculate_current_time_to_date(opt.expiry)
            if T <= 0:
                continue
            delta += p * bsd(asml_mid, opt.strike, T, RATE, SIGMA, opt.option_kind)

        for fid in stock_futs.get("ASML", []):
            delta += pos.get(fid)

        if abs(delta) > 0.5 and bok(asml_book):
            if delta > 0.5:
                lots = min(round(delta), pos.hr("ASML", "ask"), POSITION_LIMIT)
                if lots > 0:
                    e.insert("ASML", asml_book.bids[0].price, lots, "ask", "ioc")
                    pos.fill("ASML", lots, "ask")
            else:
                lots = min(round(abs(delta)), pos.hr("ASML", "bid"), POSITION_LIMIT)
                if lots > 0:
                    e.insert("ASML", asml_book.asks[0].price, lots, "bid", "ioc")
                    pos.fill("ASML", lots, "bid")

    if ob5x_futs:
        pf = ob5x_futs[0]
        pfb = e.book(pf)
        if bok(pfb):
            tau = calculate_current_time_to_date(insts[pf].expiry)
            if tau > 0:
                idx_val = bmid(pfb) / exp(RATE * tau)

                delta = ETF_M * pos.get("OB5X_ETF")
                for fid in ob5x_futs:
                    delta += pos.get(fid)

                for oid, opt in idx_opts.items():
                    p = pos.get(oid)
                    if p == 0:
                        continue
                    T = calculate_current_time_to_date(opt.expiry)
                    if T <= 0:
                        continue
                    delta += p * bsd(idx_val, opt.strike, T, RATE, SIGMA, opt.option_kind)

                if abs(delta) > 0.5:
                    if delta > 0.5:
                        lots = min(round(delta), pos.hr(pf, "ask"), POSITION_LIMIT)
                        if lots > 0:
                            e.insert(pf, pfb.bids[0].price, lots, "ask", "ioc")
                            pos.fill(pf, lots, "ask")
                    else:
                        lots = min(round(abs(delta)), pos.hr(pf, "bid"), POSITION_LIMIT)
                        if lots > 0:
                            e.insert(pf, pfb.asks[0].price, lots, "bid", "ioc")
                            pos.fill(pf, lots, "bid")


def compute_index(e) -> float | None:
    total = 0.0
    for sid, w in CONSTITUENTS.items():
        m = bmid(e.book(sid))
        if m is None:
            return None
        total += w * m
    return total / INDEX_DIVISOR


e = Ex(rate=15)
insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts = discover(e)

all_options: list[tuple[str, object, str]] = []
for base, opts in stock_opts.items():
    for oid, inst in opts.items():
        all_options.append((oid, inst, base))
for oid, inst in idx_opts.items():
    all_options.append((oid, inst, "OB5X"))

primary_fut = ob5x_futs[0] if ob5x_futs else None
const_idx: float | None = None
opt_cursor = 0
iteration = 0

log.info(f"primary_fut={primary_fut}, {len(all_options)} options, {len(duals)} dual pairs")

while True:
    try:
        if not e.is_connected():
            log.warning("disconnected, reconnecting...")
            e.reconnect()
            insts, duals, ob5x_futs, stock_futs, stock_opts, idx_opts = discover(e)
            all_options = []
            for base, opts in stock_opts.items():
                for oid, inst in opts.items():
                    all_options.append((oid, inst, base))
            for oid, inst in idx_opts.items():
                all_options.append((oid, inst, "OB5X"))
            primary_fut = ob5x_futs[0] if ob5x_futs else None
            opt_cursor = 0
            time.sleep(3)
            continue

        e.clear_cache()
        pos = Pos(e.get_positions())
        iteration += 1

        if iteration % 5 == 1:
            const_idx = compute_index(e)

        fut_idx = None
        if primary_fut:
            pfb = e.book(primary_fut)
            if bok(pfb):
                tau = calculate_current_time_to_date(insts[primary_fut].expiry)
                if tau > 0:
                    fut_idx = bmid(pfb) / exp(RATE * tau)

        run_dual(e, duals, pos, insts)

        if iteration % 2 == 0:
            run_etf(e, primary_fut, insts, pos, const_idx)

        if all_options:
            asml_mid = bmid(e.book("ASML")) if "ASML" in insts else None
            pricing_idx = fut_idx or const_idx

            for i in range(OPTIONS_PER_ITER):
                idx = (opt_cursor + i) % len(all_options)
                oid, opt, base = all_options[idx]

                if base == "OB5X":
                    u_mid = pricing_idx
                else:
                    u_mid = bmid(e.book(base)) if base in insts else None

                if u_mid is not None:
                    quote_option(e, oid, opt, u_mid, pos, insts)

            opt_cursor = (opt_cursor + OPTIONS_PER_ITER) % max(1, len(all_options))

        if iteration % 5 == 0:
            run_futures_arb(e, ob5x_futs, stock_futs, insts, pos)

        hedge_delta(e, pos, insts, stock_opts, idx_opts, ob5x_futs, stock_futs)

        if iteration % 10 == 0:
            pnl = e.get_pnl()
            active = {k: v for k, v in pos.items() if v != 0}
            log.info(f"[iter {iteration}] PnL={pnl:.2f}  {active}")

        time.sleep(0.5)

    except Exception as ex:
        log.error(f"error: {ex}")
        time.sleep(3)
