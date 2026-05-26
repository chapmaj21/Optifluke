from math import floor, ceil


DUAL_CREDIT = 0.02
DUAL_VOLUME = 15
POS_SKEW_THRESHOLD = 10
POS_SKEW_DIVISOR = 3


def _td(price: float, tick: float) -> float:
    return floor(price / tick) * tick


def _tu(price: float, tick: float) -> float:
    return ceil(price / tick) * tick


def run_dual_listing(ex, liquid: str, dual: str, pos, insts):
    lb = ex.book(liquid)
    db = ex.book(dual)
    if not (_bv(lb) and _bv(db)):
        return

    tick = insts[dual].tick_size
    lbid, lask = lb.bids[0].price, lb.asks[0].price
    dbid, dask = db.bids[0].price, db.asks[0].price

    arbed = False

    if dask < lbid:
        v = min(db.asks[0].volume, DUAL_VOLUME, pos.hr(dual, "bid"), pos.hr(liquid, "ask"))
        if v > 0:
            ex.cancel(dual)
            ex.insert(dual, dask, v, "bid", "ioc")
            pos.fill(dual, v, "bid")
            ex.cancel(liquid)
            ex.insert(liquid, lbid, v, "ask", "ioc")
            pos.fill(liquid, v, "ask")
            arbed = True

    elif dbid > lask:
        v = min(db.bids[0].volume, DUAL_VOLUME, pos.hr(dual, "ask"), pos.hr(liquid, "bid"))
        if v > 0:
            ex.cancel(dual)
            ex.insert(dual, dbid, v, "ask", "ioc")
            pos.fill(dual, v, "ask")
            ex.cancel(liquid)
            ex.insert(liquid, lask, v, "bid", "ioc")
            pos.fill(liquid, v, "bid")
            arbed = True

    if not arbed:
        ex.cancel(dual)

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
