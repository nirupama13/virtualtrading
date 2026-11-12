"""Option pricing (Black-Scholes), expiry calendar and the option chain.

All numbers are synthetic: premiums follow the virtual index, implied volatility follows
the market regime, and open interest / volume are simulated.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .market import INDICES, SESSION_MINUTES, Snapshot

RATE = 0.065
YEAR_MINUTES = 252 * SESSION_MINUTES
TICK = 0.05
KINDS = ("CE", "PE")


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def round_premium(x: float) -> float:
    return max(TICK, round(round(x / TICK) * TICK, 2))


def bs(S: float, K: float, T: float, sigma: float, kind: str) -> dict:
    """Black-Scholes price and Greeks. T in years. theta per trading day, vega per 1 vol point."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0.0) if kind == "CE" else max(K - S, 0.0)
        delta = (1.0 if S > K else 0.0) if kind == "CE" else (-1.0 if S < K else 0.0)
        return dict(price=intrinsic, delta=delta, gamma=0.0, theta=0.0, vega=0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (RATE + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    disc = math.exp(-RATE * T)
    decay = -S * _pdf(d1) * sigma / (2.0 * math.sqrt(T))
    if kind == "CE":
        price = S * _cdf(d1) - K * disc * _cdf(d2)
        delta = _cdf(d1)
        theta = decay - RATE * K * disc * _cdf(d2)
    else:
        price = K * disc * _cdf(-d2) - S * _cdf(-d1)
        delta = _cdf(d1) - 1.0
        theta = decay + RATE * K * disc * _cdf(-d2)
    return dict(price=price, delta=delta, gamma=_pdf(d1) / (S * sq), theta=theta / 252.0,
                vega=S * _pdf(d1) * math.sqrt(T) / 100.0)


# ---------------- expiry calendar ----------------
def expiry_dates(symbol: str, today: date) -> list[date]:
    """Upcoming expiries: 4 weekly expiries, or 3 monthly ones (last weekday of the month)."""
    cfg = INDICES[symbol]
    wd = cfg["expiry_weekday"]
    if cfg["weekly"]:
        first = today + timedelta(days=(wd - today.weekday()) % 7)
        return [first + timedelta(days=7 * i) for i in range(4)]
    out: list[date] = []
    y, mo = today.year, today.month
    while len(out) < 3:
        last = date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)
        d = last - timedelta(days=(last.weekday() - wd) % 7)
        if d >= today:
            out.append(d)
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return out


def minutes_to_expiry(snap: Snapshot, expiry: date) -> int:
    """Trading minutes left until 15:30 on expiry day (at least 1)."""
    remaining_today = SESSION_MINUTES - (snap.k + 1)
    if expiry <= snap.day:
        return max(remaining_today, 1)
    days = int(np.busday_count(np.datetime64(snap.day + timedelta(days=1)), np.datetime64(expiry + timedelta(days=1))))
    return remaining_today + SESSION_MINUTES * days


# ---------------- pricing ----------------
def iv_for(atm_iv: float, S: float, K: float, T: float) -> float:
    """Volatility smile with put skew: OTM puts are richer than OTM calls."""
    x = math.log(K / S) / (atm_iv * math.sqrt(max(T, 1e-4)))
    return atm_iv * float(np.clip(1.0 + 0.05 * x * x - 0.03 * x, 0.85, 2.0))


def quote(snap: Snapshot, symbol: str, expiry: date, strike: float, kind: str,
          spot: float | None = None, iv_mult: float = 1.0) -> dict:
    """Premium, IV and Greeks for one contract. `spot`/`iv_mult` allow what-if repricing."""
    S = snap.prices[symbol] if spot is None else spot
    T = minutes_to_expiry(snap, expiry) / YEAR_MINUTES
    iv = iv_for(snap.iv_atm, S, strike, T) * iv_mult
    g = bs(S, strike, T, iv, kind)
    return dict(ltp=round_premium(g["price"]), raw=g["price"], iv=iv, delta=g["delta"],
                gamma=g["gamma"], theta=g["theta"], vega=g["vega"], T=T)


def bid_ask(ltp: float) -> tuple[float, float]:
    half = max(TICK, round(round(0.0015 * ltp / TICK) * TICK, 2))
    return round_premium(ltp - half), round_premium(ltp + half)


# ---------------- option chain ----------------
def _sim_oi(seed: int, symbol: str, expiry: date, strike: float, kind: str, dist_steps: float) -> int:
    """Deterministic simulated open interest: heavier near the money, with a little noise."""
    rng = np.random.default_rng([seed, list(INDICES).index(symbol), expiry.toordinal(),
                                 int(strike), KINDS.index(kind)])
    shape = math.exp(-((dist_steps / 7.0) ** 2)) + 0.15
    return int(250_000 * shape * (0.6 + 0.8 * rng.random()) / 50) * 50


def option_chain(snap: Snapshot, seed: int, symbol: str, expiry: date, n_strikes: int = 10) -> pd.DataFrame:
    cfg = INDICES[symbol]
    step = cfg["step"]
    S = snap.prices[symbol]
    atm = round(S / step) * step
    session_frac = min(1.0, 0.05 + (snap.k + 1) / SESSION_MINUTES)
    rows = []
    for i in range(-n_strikes, n_strikes + 1):
        K = atm + i * step
        row = {"Strike": K}
        for kind in KINDS:
            q = quote(snap, symbol, expiry, K, kind)
            oi = _sim_oi(seed, symbol, expiry, K, kind, (K - cfg["start"]) / step)
            vol = int(oi * 0.35 * session_frac)
            row.update({f"{kind} LTP": q["ltp"], f"{kind} IV": q["iv"] * 100, f"{kind} Δ": q["delta"],
                        f"{kind} OI": oi, f"{kind} Vol": vol})
        rows.append(row)
    return pd.DataFrame(rows)
