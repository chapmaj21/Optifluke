from math import exp, floor, ceil

RATE = 0.03
ETF_M = 0.25
ETF_C = 2.50
ETF_CREDIT = 0.005
ETF_VOLUME = 40
ETF_ARB_EDGE = 0.01
ETF_ARB_VOLUME = 60
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


def _bv(b) -> bool:
    return b and b.bids and b.asks


def run_etf_quoting(ex, primary_fut: str, insts: dict, pos, const_idx: float | None, tau: float):
    if not primary_fut or "OB5X_ETF" not in insts:
        return

    ex.cancel("OB5X_ETF")

    fb = ex.book(primary_fut)
    if not _bv(fb):
        return

    if tau <= 0:
        tau = 1e-6

    eb = ex.book("OB5X_ETF")
    tick = insts["OB5X_ETF"].tick_size
    fbid, fask = fb.bids[0].price, fb.asks[0].price
    disc = exp(RATE * tau)

    fair_bid = ETF_C + ETF_M * (fbid / disc)
    fair_ask = ETF_C + ETF_M * (fask / disc)

    if const_idx is not None:
        from_const = ETF_C + ETF_M * const_idx
        fair_bid = min(fair_bid, from_const)
        fair_ask = max(fair_ask, from_const)

    if _bv(eb):
        cheap_edge = fair_bid - eb.asks[0].price
        rich_edge = eb.bids[0].price - fair_ask

        if cheap_edge > ETF_ARB_EDGE:
            max_by_future = int(fb.bids[0].volume * disc / ETF_M)
            v = min(ETF_ARB_VOLUME, eb.asks[0].volume, max_by_future, pos.hr("OB5X_ETF", "bid"))
            filled_etf = _ioc(ex, "OB5X_ETF", eb.asks[0].price, v, "bid", pos)
            hedge_lots = min(round(filled_etf * ETF_M / disc), fb.bids[0].volume, pos.hr(primary_fut, "ask"))
            if hedge_lots > 0:
                ex.cancel(primary_fut)
                _ioc(ex, primary_fut, fbid, hedge_lots, "ask", pos)
        elif rich_edge > ETF_ARB_EDGE:
            max_by_future = int(fb.asks[0].volume * disc / ETF_M)
            v = min(ETF_ARB_VOLUME, eb.bids[0].volume, max_by_future, pos.hr("OB5X_ETF", "ask"))
            filled_etf = _ioc(ex, "OB5X_ETF", eb.bids[0].price, v, "ask", pos)
            hedge_lots = min(round(filled_etf * ETF_M / disc), fb.asks[0].volume, pos.hr(primary_fut, "bid"))
            if hedge_lots > 0:
                ex.cancel(primary_fut)
                _ioc(ex, primary_fut, fask, hedge_lots, "bid", pos)

    bp = _td(fair_bid - ETF_CREDIT, tick)
    ap = _tu(fair_ask + ETF_CREDIT, tick)
    if bp <= 0 or ap <= 0 or bp >= ap:
        return

    ep = pos.get("OB5X_ETF")
    max_bid_by_hedge = int(fb.bids[0].volume * disc / ETF_M)
    max_ask_by_hedge = int(fb.asks[0].volume * disc / ETF_M)
    bv = min(ETF_VOLUME, max_bid_by_hedge, pos.hr("OB5X_ETF", "bid"))
    av = min(ETF_VOLUME, max_ask_by_hedge, pos.hr("OB5X_ETF", "ask"))
    if ep > POS_SKEW_THRESHOLD:
        bv = max(0, bv - ep // POS_SKEW_DIVISOR)
    elif ep < -POS_SKEW_THRESHOLD:
        av = max(0, av + ep // POS_SKEW_DIVISOR)

    if bv > 0:
        ex.insert("OB5X_ETF", bp, bv, "bid", "limit")
    if av > 0:
        ex.insert("OB5X_ETF", ap, av, "ask", "limit")
