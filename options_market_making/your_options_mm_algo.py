from math import floor, ceil

from optibook.common_types import OptionKind

import sys
sys.path.append("/home/workspace/your_optiver_workspace")
from common.black_scholes import call_value, put_value, call_delta, put_delta, call_vega, put_vega
from common.libs import calculate_current_time_to_date

RATE = 0.03
SIGMA = 3.0
OPT_VOLUME = 5
OPT_BASE_CREDIT = 0.05
OPT_VEGA_SCALE = 0.02
OPT_SPREAD_SCALE = 0.10
OPT_MIN_FLOOR = 0.10
OPT_MIN_PCT = 0.04
OPT_POS_SKEW = 0.005


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


def _compute_credit(theo: float, vega: float, spread: float) -> float:
    c = OPT_BASE_CREDIT + OPT_VEGA_SCALE * abs(vega) + OPT_SPREAD_SCALE * spread
    c = max(c, OPT_MIN_PCT * theo)
    return max(OPT_MIN_FLOOR, c)


def quote_single_option(ex, oid: str, opt, underlying_mid: float, pos, insts: dict):
    T = calculate_current_time_to_date(opt.expiry)
    if T <= 0:
        return

    tick = insts[oid].tick_size if oid in insts else 0.10
    theo = _bs_value(underlying_mid, opt.strike, T, opt.option_kind)
    vega = _bs_vega(underlying_mid, opt.strike, T, opt.option_kind)

    ob = ex.book(oid)
    spread = (ob.asks[0].price - ob.bids[0].price) if _bv(ob) else 0.0

    credit = _compute_credit(theo, vega, spread)

    opt_pos = pos.get(oid)
    skew = OPT_POS_SKEW * opt_pos
    adjusted_theo = theo - skew

    ex.cancel(oid)
    bp = _td(adjusted_theo - credit, tick)
    ap = _tu(adjusted_theo + credit, tick)

    bv = min(OPT_VOLUME, pos.hr(oid, "bid"))
    av = min(OPT_VOLUME, pos.hr(oid, "ask"))
    if opt_pos > 20:
        bv = 0
    elif opt_pos > 10:
        bv = min(bv, 1)
    if opt_pos < -20:
        av = 0
    elif opt_pos < -10:
        av = min(av, 1)

    if bv > 0 and bp > 0:
        ex.insert(oid, bp, bv, "bid", "limit")
    if av > 0 and ap > 0:
        ex.insert(oid, ap, av, "ask", "limit")


def compute_stock_delta(pos, underlying: str, stock_opts: dict, stock_futs: list, underlying_mid: float) -> float:
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
        delta += pos.get(fid)

    return delta


def compute_index_delta(pos, idx_opts: dict, ob5x_futs: list, etf_m: float, idx_val: float) -> float:
    delta = etf_m * pos.get("OB5X_ETF")

    for fid in ob5x_futs:
        delta += pos.get(fid)

    for oid, opt in idx_opts.items():
        p = pos.get(oid)
        if p == 0:
            continue
        T = calculate_current_time_to_date(opt.expiry)
        if T <= 0:
            continue
        delta += p * _bs_delta(idx_val, opt.strike, T, opt.option_kind)

    return delta
