from math import floor, ceil


DUAL_CREDIT = 0.01
DUAL_VOLUME = 20
POS_SKEW_THRESHOLD = 10
POS_SKEW_DIVISOR = 3


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


def _td(price: float, tick: float) -> float:
    return floor(price / tick) * tick


def _tu(price: float, tick: float) -> float:
    return ceil(price / tick) * tick


def run_dual_listing(ex, liquid: str, dual: str, pos, insts):
    ex.cancel(dual)

    lb = ex.book(liquid)
    db = ex.book(dual)
    if not (_bv(lb) and _bv(db)):
        return

    tick = insts[dual].tick_size
    lbid, lask = lb.bids[0].price, lb.asks[0].price
    dbid, dask = db.bids[0].price, db.asks[0].price

    if dask < lbid:
        v = min(db.asks[0].volume, lb.bids[0].volume, DUAL_VOLUME, pos.hr(dual, "bid"), pos.hr(liquid, "ask"))
        if v > 0:
            filled = _ioc(ex, dual, dask, v, "bid", pos)
            if filled > 0:
                hedge_vol = min(filled, lb.bids[0].volume, pos.hr(liquid, "ask"))
                if hedge_vol > 0:
                    ex.cancel(liquid)
                    _ioc(ex, liquid, lbid, hedge_vol, "ask", pos)

    elif dbid > lask:
        v = min(db.bids[0].volume, lb.asks[0].volume, DUAL_VOLUME, pos.hr(dual, "ask"), pos.hr(liquid, "bid"))
        if v > 0:
            filled = _ioc(ex, dual, dbid, v, "ask", pos)
            if filled > 0:
                hedge_vol = min(filled, lb.asks[0].volume, pos.hr(liquid, "bid"))
                if hedge_vol > 0:
                    ex.cancel(liquid)
                    _ioc(ex, liquid, lask, hedge_vol, "bid", pos)

    dp = pos.get(dual)
    bv = min(DUAL_VOLUME, pos.hr(dual, "bid"))
    av = min(DUAL_VOLUME, pos.hr(dual, "ask"))
    if dp > POS_SKEW_THRESHOLD:
        bv = max(0, bv - dp // POS_SKEW_DIVISOR)
    elif dp < -POS_SKEW_THRESHOLD:
        av = max(0, av + dp // POS_SKEW_DIVISOR)

    ob = _td(lbid - DUAL_CREDIT, tick)
    oa = _tu(lask + DUAL_CREDIT, tick)
    if bv > 0 and ob > 0:
        ex.insert(dual, ob, bv, "bid", "limit")
    if av > 0 and oa > 0:
        ex.insert(dual, oa, av, "ask", "limit")


def _bv(b) -> bool:
    return b and b.bids and b.asks
