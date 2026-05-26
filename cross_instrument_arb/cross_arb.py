from math import exp

from optibook.common_types import OptionKind

import sys
sys.path.append("/home/workspace/your_optiver_workspace")
from common.black_scholes import call_delta, put_delta
from common.libs import calculate_current_time_to_date

RATE = 0.03
SIGMA = 3.0
BASIS_THRESHOLD = 0.03
CALENDAR_THRESHOLD = 0.03
PARITY_THRESHOLD = 0.08
ARB_VOLUME = 5


def _ioc(ex, iid: str, price: float, volume: int, side: str, pos) -> int:
    if volume <= 0:
        return 0
    if hasattr(ex, "ioc"):
        filled = ex.ioc(iid, price, volume, side)
    else:
        ex.insert(iid, price, volume, side, "ioc")
        filled = volume
    if filled > 0:
        pos.fill(iid, filled, side)
    return filled


def _bv(b) -> bool:
    return b and b.bids and b.asks


def _bmid(b) -> float:
    return (b.bids[0].price + b.asks[0].price) / 2.0


def run_calendar_arb(ex, ob5x_futs: list, insts: dict, pos):
    for i in range(len(ob5x_futs)):
        for j in range(i + 1, len(ob5x_futs)):
            near, far = ob5x_futs[i], ob5x_futs[j]
            nb, fb = ex.book(near), ex.book(far)
            if not (_bv(nb) and _bv(fb)):
                continue
            tn = calculate_current_time_to_date(insts[near].expiry)
            tf = calculate_current_time_to_date(insts[far].expiry)
            if tn <= 0 or tf <= 0:
                continue
            carry = exp(RATE * (tf - tn))
            rich_edge = fb.bids[0].price - nb.asks[0].price * carry
            cheap_edge = nb.bids[0].price * carry - fb.asks[0].price

            if rich_edge > CALENDAR_THRESHOLD:
                v = min(ARB_VOLUME, fb.bids[0].volume, nb.asks[0].volume, pos.hr(far, "ask"), pos.hr(near, "bid"))
                if v > 0:
                    ex.cancel(far)
                    filled = _ioc(ex, far, fb.bids[0].price, v, "ask", pos)
                    hedge_vol = min(filled, nb.asks[0].volume, pos.hr(near, "bid"))
                    if hedge_vol > 0:
                        ex.cancel(near)
                        _ioc(ex, near, nb.asks[0].price, hedge_vol, "bid", pos)
                    return
            elif cheap_edge > CALENDAR_THRESHOLD:
                v = min(ARB_VOLUME, fb.asks[0].volume, nb.bids[0].volume, pos.hr(far, "bid"), pos.hr(near, "ask"))
                if v > 0:
                    ex.cancel(far)
                    filled = _ioc(ex, far, fb.asks[0].price, v, "bid", pos)
                    hedge_vol = min(filled, nb.bids[0].volume, pos.hr(near, "ask"))
                    if hedge_vol > 0:
                        ex.cancel(near)
                        _ioc(ex, near, nb.bids[0].price, hedge_vol, "ask", pos)
                    return


def run_basis_arb(ex, stock_futs: dict, insts: dict, pos):
    for stock, futs in stock_futs.items():
        sb = ex.book(stock)
        if not _bv(sb):
            continue
        for fid in futs:
            if fid not in insts:
                continue
            fbook = ex.book(fid)
            if not _bv(fbook):
                continue
            tau = calculate_current_time_to_date(insts[fid].expiry)
            if tau <= 0:
                continue
            carry = exp(RATE * tau)
            rich_edge = fbook.bids[0].price - sb.asks[0].price * carry
            cheap_edge = sb.bids[0].price * carry - fbook.asks[0].price

            if rich_edge > BASIS_THRESHOLD:
                v = min(ARB_VOLUME, fbook.bids[0].volume, sb.asks[0].volume, pos.hr(fid, "ask"), pos.hr(stock, "bid"))
                if v > 0:
                    ex.cancel(fid)
                    filled = _ioc(ex, fid, fbook.bids[0].price, v, "ask", pos)
                    hedge_vol = min(filled, sb.asks[0].volume, pos.hr(stock, "bid"))
                    if hedge_vol > 0:
                        ex.cancel(stock)
                        _ioc(ex, stock, sb.asks[0].price, hedge_vol, "bid", pos)
                    return
            elif cheap_edge > BASIS_THRESHOLD:
                v = min(ARB_VOLUME, fbook.asks[0].volume, sb.bids[0].volume, pos.hr(fid, "bid"), pos.hr(stock, "ask"))
                if v > 0:
                    ex.cancel(fid)
                    filled = _ioc(ex, fid, fbook.asks[0].price, v, "bid", pos)
                    hedge_vol = min(filled, sb.bids[0].volume, pos.hr(stock, "ask"))
                    if hedge_vol > 0:
                        ex.cancel(stock)
                        _ioc(ex, stock, sb.bids[0].price, hedge_vol, "ask", pos)
                    return


def run_parity_arb(ex, option_pairs: dict, insts: dict, pos):
    for (underlying, expiry, strike), kinds in option_pairs.items():
        if OptionKind.CALL not in kinds or OptionKind.PUT not in kinds:
            continue

        call_id = kinds[OptionKind.CALL]
        put_id = kinds[OptionKind.PUT]

        cb = ex.book(call_id)
        pb = ex.book(put_id)
        sb = ex.book(underlying)

        if not (_bv(cb) and _bv(pb) and _bv(sb)):
            continue

        tau = calculate_current_time_to_date(expiry)
        if tau <= 0:
            continue

        pv_strike = strike * exp(-RATE * tau)
        rich_edge = (cb.bids[0].price - pb.asks[0].price) - (sb.asks[0].price - pv_strike)
        cheap_edge = (sb.bids[0].price - pv_strike) - (cb.asks[0].price - pb.bids[0].price)

        s_mid = _bmid(sb)

        if rich_edge > PARITY_THRESHOLD:
            v = min(
                ARB_VOLUME,
                cb.bids[0].volume,
                pb.asks[0].volume,
                sb.asks[0].volume,
                pos.hr(call_id, "ask"),
                pos.hr(put_id, "bid"),
                pos.hr(underlying, "bid"),
            )
            if v > 0:
                ex.cancel(call_id)
                filled_call = _ioc(ex, call_id, cb.bids[0].price, v, "ask", pos)
                if filled_call <= 0:
                    return
                ex.cancel(put_id)
                filled_put = _ioc(ex, put_id, pb.asks[0].price, min(filled_call, pb.asks[0].volume, pos.hr(put_id, "bid")), "bid", pos)
                d_call = call_delta(s_mid, strike, tau, RATE, SIGMA)
                d_put = put_delta(s_mid, strike, tau, RATE, SIGMA)
                net_delta = -filled_call * d_call + filled_put * d_put
                hedge_lots = round(abs(net_delta))
                hv = min(hedge_lots, sb.asks[0].volume, pos.hr(underlying, "bid"))
                if hv > 0:
                    _ioc(ex, underlying, sb.asks[0].price, hv, "bid", pos)
                return

        elif cheap_edge > PARITY_THRESHOLD:
            v = min(
                ARB_VOLUME,
                cb.asks[0].volume,
                pb.bids[0].volume,
                sb.bids[0].volume,
                pos.hr(call_id, "bid"),
                pos.hr(put_id, "ask"),
                pos.hr(underlying, "ask"),
            )
            if v > 0:
                ex.cancel(call_id)
                filled_call = _ioc(ex, call_id, cb.asks[0].price, v, "bid", pos)
                if filled_call <= 0:
                    return
                ex.cancel(put_id)
                filled_put = _ioc(ex, put_id, pb.bids[0].price, min(filled_call, pb.bids[0].volume, pos.hr(put_id, "ask")), "ask", pos)
                d_call = call_delta(s_mid, strike, tau, RATE, SIGMA)
                d_put = put_delta(s_mid, strike, tau, RATE, SIGMA)
                net_delta = filled_call * d_call - filled_put * d_put
                hedge_lots = round(abs(net_delta))
                hv = min(hedge_lots, sb.bids[0].volume, pos.hr(underlying, "ask"))
                if hv > 0:
                    _ioc(ex, underlying, sb.bids[0].price, hv, "ask", pos)
                return


def run_cross_arb(ex, ob5x_futs, stock_futs, option_pairs, insts, pos, sub_cursor: int):
    phase = sub_cursor % 3
    if phase == 0:
        run_calendar_arb(ex, ob5x_futs, insts, pos)
    elif phase == 1:
        run_basis_arb(ex, stock_futs, insts, pos)
    else:
        run_parity_arb(ex, option_pairs, insts, pos)
