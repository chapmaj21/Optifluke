from math import exp, floor, ceil

from optibook.common_types import OptionKind

import sys
sys.path.append("/home/workspace/your_optiver_workspace")
from common.black_scholes import call_value, put_value, call_delta, put_delta, call_vega, put_vega
from common.libs import calculate_current_time_to_date

RATE = 0.03
SIGMA = 3.0
OPT_VOLUME = 8
OPT_BASE_CREDIT = 0.03
OPT_VEGA_SCALE = 0.006
OPT_SPREAD_SCALE = 0.05
OPT_UNDERLYING_SPREAD_SCALE = 0.35
OPT_MIN_FLOOR = 0.05
OPT_MIN_PCT = 0.006
OPT_MAX_PCT = 0.025
OPT_POS_SKEW = 0.010


def _td(price: float, tick: float) -> float:
    return floor(price / tick) * tick


def _tu(price: float, tick: float) -> float:
    return ceil(price / tick) * tick


def _bv(b) -> bool:
    return b and b.bids and b.asks


def _bs_value(S, K, T, kind):
    return (call_value if kind == OptionKind.CALL else put_value)(S=S, K=K, T=T, r=RATE, sigma=SIGMA)


def _bs_delta(S, K, T, kind):
    return (call_delta if kind == OptionKind.CALL else put_delta)(S=S, K=K, T=T, r=RATE, sigma=SIGMA)


def _bs_vega(S, K, T, kind):
    return (call_vega if kind == OptionKind.CALL else put_vega)(S=S, K=K, T=T, r=RATE, sigma=SIGMA)


def _compute_credit(theo: float, vega: float, option_spread: float, underlying_spread: float) -> float:
    c = (
        OPT_BASE_CREDIT
        + OPT_VEGA_SCALE * abs(vega)
        + OPT_SPREAD_SCALE * max(0.0, min(option_spread, 2.0))
        + OPT_UNDERLYING_SPREAD_SCALE * max(0.0, underlying_spread)
    )
    theo_floor = max(theo, 1.0)
    c = max(c, OPT_MIN_PCT * theo_floor, OPT_MIN_FLOOR)
    return min(c, max(0.25, OPT_MAX_PCT * theo_floor))


def quote_single_option(
    ex,
    oid: str,
    opt,
    underlying_mid: float,
    pos,
    insts: dict,
    underlying_bid: float | None = None,
    underlying_ask: float | None = None,
):
    T = calculate_current_time_to_date(opt.expiry)
    if T <= 0 or underlying_mid <= 0:
        return

    underlying_bid = underlying_mid if underlying_bid is None else underlying_bid
    underlying_ask = underlying_mid if underlying_ask is None else underlying_ask
    if underlying_bid <= 0 or underlying_ask <= 0:
        return

    tick = insts[oid].tick_size if oid in insts else 0.10
    theo = _bs_value(underlying_mid, opt.strike, T, opt.option_kind)
    vega = _bs_vega(underlying_mid, opt.strike, T, opt.option_kind)

    if opt.option_kind == OptionKind.CALL:
        fair_bid = _bs_value(underlying_bid, opt.strike, T, opt.option_kind)
        fair_ask = _bs_value(underlying_ask, opt.strike, T, opt.option_kind)
    else:
        fair_bid = _bs_value(underlying_ask, opt.strike, T, opt.option_kind)
        fair_ask = _bs_value(underlying_bid, opt.strike, T, opt.option_kind)

    ex.cancel(oid)
    ob = ex.book(oid)
    spread = (ob.asks[0].price - ob.bids[0].price) if _bv(ob) else 0.0
    underlying_spread = max(0.0, underlying_ask - underlying_bid)

    credit = _compute_credit(theo, vega, spread, underlying_spread)

    opt_pos = pos.get(oid)
    skew = OPT_POS_SKEW * opt_pos

    bp = _td(fair_bid - credit - skew, tick)
    ap = _tu(fair_ask + credit - skew, tick)
    if bp >= ap:
        bp = _td(theo - credit - skew, tick)
        ap = _tu(theo + credit - skew, tick)

    bv = min(OPT_VOLUME, pos.hr(oid, "bid"))
    av = min(OPT_VOLUME, pos.hr(oid, "ask"))
    if opt_pos > 50:
        bv = 0
        av = min(av + 4, pos.hr(oid, "ask"))
    elif opt_pos > 25:
        bv = min(bv, 1)
        av = min(av + 2, pos.hr(oid, "ask"))
    if opt_pos < -50:
        av = 0
        bv = min(bv + 4, pos.hr(oid, "bid"))
    elif opt_pos < -25:
        av = min(av, 1)
        bv = min(bv + 2, pos.hr(oid, "bid"))

    if bv > 0 and bp > 0:
        ex.insert(oid, bp, bv, "bid", "limit")
    if av > 0 and ap > 0:
        ex.insert(oid, ap, av, "ask", "limit")


def compute_stock_delta(pos, underlying: str, stock_opts: dict, stock_futs: list, underlying_mid: float, insts: dict | None = None) -> float:
    delta = float(pos.get(underlying))

    dual_id = underlying + "_DUAL"
    delta += pos.get(dual_id)

    for oid, opt in stock_opts.items():
        p = pos.get(oid)
        if p == 0:
            continue
        T = calculate_current_time_to_date(opt.expiry)
        if T <= 0:
            continue
        delta += p * _bs_delta(underlying_mid, opt.strike, T, opt.option_kind)

    for fid in stock_futs:
        hedge_unit = 1.0
        if insts is not None and fid in insts:
            T = calculate_current_time_to_date(insts[fid].expiry)
            if T > 0:
                hedge_unit = exp(RATE * T)
        delta += pos.get(fid) * hedge_unit

    return delta


def compute_index_delta(pos, idx_opts: dict, ob5x_futs: list, etf_m: float, idx_val: float, insts: dict | None = None) -> float:
    delta = etf_m * pos.get("OB5X_ETF")

    for fid in ob5x_futs:
        hedge_unit = 1.0
        if insts is not None and fid in insts:
            T = calculate_current_time_to_date(insts[fid].expiry)
            if T > 0:
                hedge_unit = exp(RATE * T)
        delta += pos.get(fid) * hedge_unit

    for oid, opt in idx_opts.items():
        p = pos.get(oid)
        if p == 0:
            continue
        T = calculate_current_time_to_date(opt.expiry)
        if T <= 0:
            continue
        delta += p * _bs_delta(idx_val, opt.strike, T, opt.option_kind)

    return delta
