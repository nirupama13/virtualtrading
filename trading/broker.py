"""Virtual broker: orders, positions, margin, charges and expiry settlement.

Everything is paper money. Rules (simplified on purpose):
  * Market orders fill at the ask (buy) or bid (sell); limit orders fill when the bid/ask crosses.
  * Margin is scenario based (a mini-SPAN): the portfolio is repriced for spot moves of up to +/-12%
    and the worst loss is blocked. Hedged positions (spreads, straddles + wings) therefore need less.
  * Positions expire at 15:30 on expiry day and settle at intrinsic value.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import date

from engine.market import INDICES, SESSION_MINUTES, Market, Snapshot
from engine.options import KINDS, bid_ask, expiry_dates, quote

STARTING_CAPITAL = 1_000_000.0
BROKERAGE = 20.0
STT_SELL = 0.001
TXN_CHARGE = 0.0003503
GST = 0.18
STAMP_BUY = 0.00003
SHOCKS = (-0.12, -0.08, -0.05, -0.02, 0.0, 0.02, 0.05, 0.08, 0.12)
MARGIN_FLOOR = 0.005  # of net short notional


@dataclass
class Position:
    symbol: str
    expiry: date
    strike: int
    kind: str
    lots: int       # signed: + long, - short
    avg: float

    @property
    def key(self) -> tuple:
        return (self.symbol, self.expiry, self.strike, self.kind)

    @property
    def qty(self) -> int:
        return self.lots * INDICES[self.symbol]["lot"]


def label(symbol: str, expiry: date, strike: int, kind: str) -> str:
    return f"{symbol} {expiry:%d%b%y} {strike} {kind}".upper()


class Account:
    def __init__(self, capital: float = STARTING_CAPITAL):
        self.capital = capital
        self.cash = capital
        self.realized = 0.0
        self.charges = 0.0
        self.positions: dict[tuple, Position] = {}
        self.orders: list[dict] = []
        self.trades: list[dict] = []
        self.lock = threading.RLock()
        self._oid = 0

    def next_id(self) -> int:
        self._oid += 1
        return self._oid


def charges_for(price: float, qty: int, side: str) -> float:
    turnover = price * qty
    txn = TXN_CHARGE * turnover
    total = BROKERAGE + txn + GST * (BROKERAGE + txn)
    total += STT_SELL * turnover if side == "SELL" else STAMP_BUY * turnover
    return round(total, 2)


# ---------------- valuation & margin ----------------
def _value(positions: dict, snap: Snapshot, shock: float = 0.0) -> float:
    total = 0.0
    for p in positions.values():
        spot = snap.prices[p.symbol] * (1 + shock)
        q = quote(snap, p.symbol, p.expiry, p.strike, p.kind, spot=spot, iv_mult=1 + 0.02 * abs(shock) * 100)
        total += p.qty * q["ltp"]
    return total


def margin_required(positions: dict, snap: Snapshot) -> float:
    """Worst-case portfolio loss over spot shocks, plus a small floor on net short exposure."""
    if not positions:
        return 0.0
    # Reprice by symbol so each underlying shocks independently, then add the symbols up.
    total = 0.0
    for sym in {p.symbol for p in positions.values()}:
        sub = {k: p for k, p in positions.items() if p.symbol == sym}
        v_min = min(_value(sub, snap, s) for s in SHOCKS)
        short_q = sum(-p.qty for p in sub.values() if p.qty < 0)
        long_q = sum(p.qty for p in sub.values() if p.qty > 0)
        floor = MARGIN_FLOOR * snap.prices[sym] * max(0, short_q - long_q)
        total += max(0.0, -v_min) + floor
    return total


def summary(acct: Account, snap: Snapshot) -> dict:
    with acct.lock:
        value = _value(acct.positions, snap)
        unrealized = sum((quote(snap, p.symbol, p.expiry, p.strike, p.kind)["ltp"] - p.avg) * p.qty
                         for p in acct.positions.values())
        margin = margin_required(acct.positions, snap)
        equity = acct.cash + value
        return dict(cash=acct.cash, equity=equity, pnl=equity - acct.capital, realized=acct.realized,
                    unrealized=unrealized, margin=margin, available=acct.cash - margin,
                    charges=acct.charges)


def positions_table(acct: Account, snap: Snapshot) -> list[dict]:
    rows = []
    with acct.lock:
        for p in acct.positions.values():
            q = quote(snap, p.symbol, p.expiry, p.strike, p.kind)
            rows.append({
                "Contract": label(*p.key), "Lots": p.lots, "Qty": p.qty, "Avg": round(p.avg, 2),
                "LTP": q["ltp"], "P&L": round((q["ltp"] - p.avg) * p.qty, 2),
                "Delta": round(q["delta"] * p.qty, 1), "Theta/day": round(q["theta"] * p.qty, 1),
                "_key": p.key,
            })
    return rows


# ---------------- order evaluation ----------------
def _apply(positions: dict, key: tuple, signed_lots: int, price: float) -> tuple[dict, float]:
    """Return (new positions, realized P&L) after trading `signed_lots` at `price`."""
    new = {k: replace(p) for k, p in positions.items()}
    realized = 0.0
    pos = new.get(key)
    if pos is None:
        new[key] = Position(*key, lots=signed_lots, avg=price)
        return new, 0.0
    if pos.lots * signed_lots > 0:
        total = abs(pos.lots) + abs(signed_lots)
        pos.avg = (pos.avg * abs(pos.lots) + price * abs(signed_lots)) / total
        pos.lots += signed_lots
        return new, 0.0
    closed = min(abs(signed_lots), abs(pos.lots))
    sign = 1 if pos.lots > 0 else -1
    realized = (price - pos.avg) * sign * closed * INDICES[pos.symbol]["lot"]
    remaining = pos.lots + signed_lots
    if remaining == 0:
        del new[key]
    else:
        if (remaining > 0) != (pos.lots > 0):  # flipped side: new average is the fill price
            pos.avg = price
        pos.lots = remaining
    return new, realized


def _validate(snap: Snapshot, symbol: str, expiry: date, strike: int, kind: str, lots: int) -> str | None:
    if symbol not in INDICES:
        return "Unknown index"
    if kind not in KINDS:
        return "Option type must be CE or PE"
    if not isinstance(lots, int) or lots < 1:
        return "Lots must be a whole number >= 1"
    if lots > 1000:
        return "Max 1000 lots per order"
    if strike % INDICES[symbol]["step"] != 0 or strike <= 0:
        return f"Strike must be a multiple of {INDICES[symbol]['step']}"
    if expiry not in expiry_dates(symbol, snap.day):
        return "Contract is not listed"
    return None


def _evaluate(acct: Account, snap: Snapshot, symbol, expiry, strike, kind, side, lots,
              price: float | None = None) -> dict:
    """Dry-run of an order: price, charges and funds check. Does not change the account."""
    err = _validate(snap, symbol, expiry, strike, kind, lots)
    if err:
        return dict(ok=False, msg=err)
    q = quote(snap, symbol, expiry, strike, kind)
    bid, ask = bid_ask(q["ltp"])
    fill = price if price is not None else (ask if side == "BUY" else bid)
    signed = lots if side == "BUY" else -lots
    key = (symbol, expiry, strike, kind)
    new_pos, realized = _apply(acct.positions, key, signed, fill)
    qty = lots * INDICES[symbol]["lot"]
    fees = charges_for(fill, qty, side)
    cash_after = acct.cash + (-fill * qty if side == "BUY" else fill * qty) - fees
    margin_after = margin_required(new_pos, snap)
    avail_after = cash_after - margin_after
    avail_before = acct.cash - margin_required(acct.positions, snap)
    ok = avail_after >= 0 or avail_after >= avail_before  # risk-reducing orders are always allowed
    msg = "OK" if ok else f"Insufficient funds: need ₹{-avail_after:,.0f} more"
    return dict(ok=ok, msg=msg, price=fill, bid=bid, ask=ask, qty=qty, charges=fees, realized=realized,
                cash_after=cash_after, margin_after=margin_after, avail_after=avail_after,
                new_positions=new_pos, key=key)


def preview_order(acct: Account, m: Market, symbol, expiry, strike, kind, side, lots) -> dict:
    snap = m.snapshot()
    with acct.lock:
        return _evaluate(acct, snap, symbol, expiry, strike, kind, side, lots)


def _stamp(snap: Snapshot) -> str:
    h, mi = divmod(9 * 60 + 15 + snap.k, 60)
    return f"{snap.day:%d-%b} {h:02d}:{mi:02d}"


def _commit(acct: Account, snap: Snapshot, ev: dict, side: str, lots: int, kind_label: str) -> None:
    acct.cash = ev["cash_after"]
    acct.realized += ev["realized"]
    acct.charges += ev["charges"]
    acct.positions = ev["new_positions"]
    acct.trades.append({"Time": _stamp(snap), "Contract": label(*ev["key"]), "Side": side, "Lots": lots,
                        "Price": round(ev["price"], 2), "Charges": ev["charges"],
                        "Realized": round(ev["realized"], 2), "Type": kind_label})


def place_order(acct: Account, m: Market, symbol: str, expiry: date, strike: int, kind: str, side: str,
                lots: int, order_type: str = "MARKET", limit: float | None = None) -> tuple[bool, str]:
    snap = m.snapshot()
    with acct.lock:
        settle_expired(acct, m, snap)
        if order_type == "LIMIT":
            err = _validate(snap, symbol, expiry, strike, kind, lots)
            if err or not limit or limit <= 0:
                return False, err or "Enter a limit price"
            acct.orders.append(dict(id=acct.next_id(), symbol=symbol, expiry=expiry, strike=strike, kind=kind,
                                    side=side, lots=lots, limit=float(limit), status="OPEN"))
            return True, f"Limit order placed: {side} {lots} lot(s) {label(symbol, expiry, strike, kind)} @ {limit:.2f}"
        ev = _evaluate(acct, snap, symbol, expiry, strike, kind, side, lots)
        if not ev["ok"]:
            return False, ev["msg"]
        _commit(acct, snap, ev, side, lots, "MARKET")
        return True, f"{side} {lots} lot(s) {label(*ev['key'])} filled @ {ev['price']:.2f}"


def process_pending(acct: Account, m: Market) -> list[str]:
    """Fill or cancel resting limit orders. Call this on every refresh."""
    snap = m.snapshot()
    notes: list[str] = []
    with acct.lock:
        settle_expired(acct, m, snap)
        for o in acct.orders:
            if o["status"] != "OPEN":
                continue
            if o["expiry"] not in expiry_dates(o["symbol"], snap.day):
                o["status"] = "CANCELLED (expired)"
                continue
            q = quote(snap, o["symbol"], o["expiry"], o["strike"], o["kind"])
            bid, ask = bid_ask(q["ltp"])
            hit = (o["side"] == "BUY" and ask <= o["limit"]) or (o["side"] == "SELL" and bid >= o["limit"])
            if not hit:
                continue
            fill = ask if o["side"] == "BUY" else bid
            ev = _evaluate(acct, snap, o["symbol"], o["expiry"], o["strike"], o["kind"], o["side"], o["lots"], fill)
            if ev["ok"]:
                _commit(acct, snap, ev, o["side"], o["lots"], "LIMIT")
                o["status"] = "FILLED"
                notes.append(f"Limit order filled: {o['side']} {label(*ev['key'])} @ {fill:.2f}")
            else:
                o["status"] = f"REJECTED ({ev['msg']})"
    return notes


def cancel_order(acct: Account, order_id: int) -> None:
    with acct.lock:
        for o in acct.orders:
            if o["id"] == order_id and o["status"] == "OPEN":
                o["status"] = "CANCELLED"


def square_off(acct: Account, m: Market, key: tuple) -> tuple[bool, str]:
    with acct.lock:
        pos = acct.positions.get(key)
        if pos is None:
            return False, "No such position"
        side = "SELL" if pos.lots > 0 else "BUY"
        return place_order(acct, m, *key, side, abs(pos.lots))


def square_off_all(acct: Account, m: Market) -> str:
    with acct.lock:
        # close shorts first: buying back frees margin before long legs are sold
        keys = sorted(acct.positions, key=lambda k: acct.positions[k].lots > 0)
        results = [square_off(acct, m, k) for k in keys]
    bad = [msg for ok, msg in results if not ok]
    return "All positions closed" if not bad else "Some positions could not be closed: " + "; ".join(bad)


# ---------------- expiry ----------------
def settle_expired(acct: Account, m: Market, snap: Snapshot | None = None) -> None:
    """Cash-settle positions whose expiry session (15:30) has finished, at intrinsic value."""
    snap = snap or m.snapshot()
    with acct.lock:
        for key, p in list(acct.positions.items()):
            last_idx = m.day_index(p.expiry) * SESSION_MINUTES + SESSION_MINUTES - 1
            if snap.n - 1 < last_idx:
                continue
            spot = m.close_at(p.symbol, last_idx)
            if spot is None:
                continue
            intrinsic = max(spot - p.strike, 0.0) if p.kind == "CE" else max(p.strike - spot, 0.0)
            realized = (intrinsic - p.avg) * p.qty
            acct.cash += intrinsic * p.qty
            acct.realized += realized
            acct.trades.append({"Time": f"{p.expiry:%d-%b} 15:30", "Contract": label(*key), "Side": "EXPIRY",
                                "Lots": abs(p.lots), "Price": round(intrinsic, 2), "Charges": 0.0,
                                "Realized": round(realized, 2), "Type": f"SETTLED @ {spot:,.2f}"})
            del acct.positions[key]
