import math
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from engine.market import INDICES, SESSION_MINUTES, Market
from engine.options import RATE, bs, expiry_dates, option_chain, quote
from trading import broker
from trading.broker import Account


def mk(seed=11, minutes=0):
    m = Market(seed=seed, warmup=minutes)
    m.speed = 0  # freeze time: tests control exactly how many candles exist
    return m


def advance(m, minutes):
    m._virt_seconds += minutes * 60
    m.advance()


def atm_contract(m, symbol="NIFTY", kind="CE", offset=0):
    snap = m.snapshot()
    exp = expiry_dates(symbol, snap.day)[0]
    step = INDICES[symbol]["step"]
    return exp, int(round(snap.prices[symbol] / step) * step) + offset * step, kind


def test_put_call_parity():
    S, K, T, s = 24000.0, 24100.0, 0.05, 0.15
    c, p = bs(S, K, T, s, "CE")["price"], bs(S, K, T, s, "PE")["price"]
    assert c - p == pytest.approx(S - K * math.exp(-RATE * T), abs=1e-6)


def test_greeks_sane():
    for kind in ("CE", "PE"):
        g = bs(24000, 24000, 0.02, 0.15, kind)
        assert g["gamma"] > 0 and g["vega"] > 0 and g["theta"] < 0
    assert 0 < bs(24000, 24000, 0.02, 0.15, "CE")["delta"] < 1
    assert -1 < bs(24000, 24000, 0.02, 0.15, "PE")["delta"] < 0


def test_expiry_calendar():
    today = date(2026, 11, 11)  # Wednesday
    nifty = expiry_dates("NIFTY", today)
    assert [d.weekday() for d in nifty] == [1] * 4 and nifty[0] == date(2026, 11, 17)
    sensex = expiry_dates("SENSEX", today)
    assert all(d.weekday() == 3 for d in sensex)
    bank = expiry_dates("BANKNIFTY", today)
    assert bank[0] == date(2026, 11, 24) and all(d.weekday() == 1 for d in bank) and len(bank) == 3


def test_chain_shape_and_monotonic():
    m = mk()
    snap = m.snapshot()
    exp = expiry_dates("NIFTY", snap.day)[1]
    ch = option_chain(snap, m.seed, "NIFTY", exp, 8)
    assert len(ch) == 17
    assert ch["CE LTP"].is_monotonic_decreasing  # higher strike -> cheaper call
    assert ch["PE LTP"].is_monotonic_increasing
    assert (ch["CE Δ"].between(0, 1)).all() and (ch["PE Δ"].between(-1, 0)).all()


def test_buy_then_sell_round_trip_costs_money():
    m, a = mk(), Account()
    exp, K, kind = atm_contract(m)
    ok, _ = broker.place_order(a, m, "NIFTY", exp, K, kind, "BUY", 2)
    assert ok and a.positions and a.cash < broker.STARTING_CAPITAL
    ok, _ = broker.place_order(a, m, "NIFTY", exp, K, kind, "SELL", 2)
    assert ok and not a.positions
    # spread + charges mean a flat round trip loses a little
    assert a.cash < broker.STARTING_CAPITAL and a.realized < 0


def test_short_requires_margin_and_hedge_reduces_it():
    m, a = mk(), Account()
    exp, K, _ = atm_contract(m)
    snap = m.snapshot()
    broker.place_order(a, m, "NIFTY", exp, K, "CE", "SELL", 1)
    naked = broker.summary(a, snap)["margin"]
    assert naked > 50_000
    # buy a far OTM call -> call spread; margin must fall
    broker.place_order(a, m, "NIFTY", exp, K + 4 * 50, "CE", "BUY", 1)
    hedged = broker.summary(a, snap)["margin"]
    assert hedged < naked * 0.5


def test_order_rejected_when_funds_short():
    m, a = mk(), Account(capital=5_000)
    exp, K, _ = atm_contract(m)
    ok, msg = broker.place_order(a, m, "NIFTY", exp, K, "CE", "SELL", 5)
    assert not ok and "Insufficient" in msg
    ok, _ = broker.place_order(a, m, "NIFTY", exp, K, "CE", "BUY", 500)
    assert not ok and not a.positions


def test_flip_position_and_average():
    m, a = mk(), Account()
    exp, K, kind = atm_contract(m)
    broker.place_order(a, m, "NIFTY", exp, K, kind, "BUY", 2)
    broker.place_order(a, m, "NIFTY", exp, K, kind, "SELL", 5)  # closes 2, opens 3 short
    pos = next(iter(a.positions.values()))
    assert pos.lots == -3


def test_limit_order_fills_only_when_price_reached():
    m, a = mk(), Account()
    exp, K, kind = atm_contract(m)
    q = quote(m.snapshot(), "NIFTY", exp, K, kind)["ltp"]
    broker.place_order(a, m, "NIFTY", exp, K, kind, "BUY", 1, "LIMIT", limit=q * 0.5)
    assert broker.process_pending(a, m) == [] and not a.positions
    broker.place_order(a, m, "NIFTY", exp, K, kind, "BUY", 1, "LIMIT", limit=q * 2)
    notes = broker.process_pending(a, m)
    assert len(notes) == 1 and len(a.positions) == 1


def test_expiry_settlement_at_intrinsic():
    m, a = mk(), Account()
    exp, K, kind = atm_contract(m, kind="CE", offset=-2)  # ITM-ish call on first expiry
    broker.place_order(a, m, "NIFTY", exp, K, kind, "BUY", 1)
    assert a.positions
    day = m.day_index(exp)
    need = (day + 1) * SESSION_MINUTES + 5 - len(m.regime_log)
    advance(m, need)
    broker.settle_expired(a, m)
    assert not a.positions
    last = m.close_at("NIFTY", day * SESSION_MINUTES + SESSION_MINUTES - 1)
    settled = [t for t in a.trades if t["Side"] == "EXPIRY"][0]
    assert settled["Price"] == pytest.approx(max(last - K, 0), abs=0.01)
    # equity is cash only now: capital + realized - charges
    s = broker.summary(a, m.snapshot())
    assert s["equity"] == pytest.approx(a.capital + a.realized - a.charges, abs=0.01)
