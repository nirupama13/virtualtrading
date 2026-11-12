"""Virtual market engine: generates an independent, fully synthetic market.

One `Market` holds all indices (Nifty, Bank Nifty, Sensex). They share a market-wide
regime (bullish / bearish / sideways / volatile) and a common shock, so they move
together like real indices, but nothing here has any link to live market data.

Candles are generated lazily, one per real minute (times `speed`), so every viewer of
the same `Market` object sees exactly the same history.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

SESSION_MINUTES = 375  # 09:15 -> 15:30
TICK = 0.05

# expiry_weekday: 0=Mon .. 4=Fri. weekly=False -> monthly expiry (last such weekday of the month)
INDICES = {
    "NIFTY": dict(name="Nifty 50", start=24000.0, step=50, lot=65, vol=1.0, expiry_weekday=1, weekly=True),
    "BANKNIFTY": dict(name="Bank Nifty", start=52000.0, step=100, lot=30, vol=1.3, expiry_weekday=1, weekly=False),
    "SENSEX": dict(name="Sensex", start=80000.0, step=100, lot=20, vol=0.95, expiry_weekday=3, weekly=True),
}

# Market-wide at-the-money implied volatility the market drifts toward in each regime
IV_TARGET = {"sideways": 0.12, "bullish": 0.14, "bearish": 0.17, "volatile": 0.26}


@dataclass(frozen=True)
class Regime:
    mu: float        # drift per minute (log-return)
    sigma: float     # volatility per minute
    revert: float    # mean reversion strength toward regime anchor
    jump_p: float    # probability of a jump per minute
    jump_s: float    # jump size (std dev)
    dur: tuple       # (min, max) duration in minutes in auto mode


REGIMES = {
    "bullish": Regime(mu=4e-5, sigma=4.5e-4, revert=0.0, jump_p=0.002, jump_s=1.5e-3, dur=(30, 90)),
    "bearish": Regime(mu=-4e-5, sigma=5.0e-4, revert=0.0, jump_p=0.003, jump_s=1.8e-3, dur=(30, 90)),
    "sideways": Regime(mu=0.0, sigma=3.0e-4, revert=0.03, jump_p=0.0005, jump_s=1.0e-3, dur=(40, 120)),
    "volatile": Regime(mu=0.0, sigma=1.0e-3, revert=0.0, jump_p=0.02, jump_s=2.5e-3, dur=(15, 45)),
}
AUTO_WEIGHTS = {"sideways": 0.35, "bullish": 0.25, "bearish": 0.25, "volatile": 0.15}

SCENARIOS = ["auto", *REGIMES]


def _round_tick(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


@dataclass(frozen=True)
class Snapshot:
    """Consistent view of the market at one instant (so a page never mixes two candles)."""
    n: int            # number of 1-minute candles so far
    day: date         # virtual trading date of the latest candle
    k: int            # minute-of-session (0..374) of the latest candle
    prices: dict      # symbol -> last price
    iv_atm: float     # market-wide ATM implied volatility
    regime: str


class Market:
    def __init__(self, seed: int | None = None, scenario: str = "auto", speed: float = 1.0, warmup: int = 0):
        self.seed = seed if seed is not None else int(np.random.SeedSequence().entropy % (2**32))
        self.rng = np.random.default_rng(self.seed)
        self.scenario = scenario
        self.speed = speed
        self.warmup = warmup  # minutes of history generated up-front so charts are not empty
        self.epoch = 0
        self.iv_atm = 0.14
        self.lock = threading.RLock()

        self.prices = {k: v["start"] for k, v in INDICES.items()}
        self.anchor = dict(self.prices)
        self.candles: dict[str, list[tuple]] = {k: [] for k in INDICES}
        self.regime_log: list[str] = []
        self.regime = "sideways"
        self._left = 0

        today = pd.Timestamp.today().normalize()
        self.start_date = today if today.day_of_week < 5 else today + pd.offsets.BDay(1)

        self._virt_seconds = warmup * 60.0
        self._last = time.monotonic()
        self.advance()  # first candle is available immediately

    # ---------- admin controls ----------
    def set_scenario(self, scenario: str) -> None:
        with self.lock:
            if scenario not in SCENARIOS:
                raise ValueError(scenario)
            self.scenario = scenario
            self._left = 0  # pick a new regime on the next candle

    def set_speed(self, speed: float) -> None:
        with self.lock:
            self._tick_clock()
            self.speed = max(0.0, float(speed))

    # ---------- time ----------
    def _tick_clock(self) -> None:
        now = time.monotonic()
        self._virt_seconds += (now - self._last) * self.speed
        self._last = now

    def advance(self) -> int:
        """Generate any candles that are due. Returns the number of candles so far."""
        with self.lock:
            self._tick_clock()
            target = int(self._virt_seconds // 60) + 1
            n = len(self.regime_log)
            while n < target:
                self._step()
                n += 1
            return n

    # ---------- generation ----------
    def _next_regime(self) -> None:
        if self.scenario == "auto":
            names = [r for r in AUTO_WEIGHTS if r != self.regime]
            w = np.array([AUTO_WEIGHTS[r] for r in names])
            self.regime = str(self.rng.choice(names, p=w / w.sum()))
        else:
            self.regime = self.scenario
        lo, hi = REGIMES[self.regime].dur
        self._left = int(self.rng.integers(lo, hi + 1))
        self.anchor = dict(self.prices)

    def _step(self) -> None:
        if self._left <= 0:
            self._next_regime()
        self._left -= 1
        r = REGIMES[self.regime]

        common = self.rng.standard_normal()
        jump = self.rng.normal(0, r.jump_s) if self.rng.random() < r.jump_p else 0.0

        for key, cfg in INDICES.items():
            p = self.prices[key]
            z = 0.8 * common + 0.6 * self.rng.standard_normal()
            sig = r.sigma * cfg["vol"]
            drift = r.mu - r.revert * np.log(p / self.anchor[key])
            ret = drift + sig * z + jump * cfg["vol"]

            # intrabar path (6 sub-steps that sum to `ret`) gives realistic wicks
            noise = self.rng.standard_normal(6)
            steps = ret / 6 + sig * 0.45 * (noise - noise.mean())
            path = p * np.exp(np.cumsum(steps))

            o = p
            c = float(path[-1])
            h = float(max(o, path.max()))
            l = float(min(o, path.min()))
            vol = int(self.rng.lognormal(11.5, 0.3) * (1 + 3 * abs(ret) / sig))

            o, h, l, c = (_round_tick(x) for x in (o, h, l, c))
            self.candles[key].append((o, h, l, c, vol))
            self.prices[key] = c
        self.regime_log.append(self.regime)

        target = IV_TARGET[self.regime]
        self.iv_atm = float(np.clip(
            self.iv_atm + 0.03 * (target - self.iv_atm) + 0.0012 * self.rng.standard_normal(), 0.08, 0.5))

    # ---------- reading ----------
    def timestamps(self, n: int) -> pd.DatetimeIndex:
        days = (n - 1) // SESSION_MINUTES + 1
        dates = pd.bdate_range(self.start_date, periods=days)
        idx = np.arange(n)
        base = dates[idx // SESSION_MINUTES] + pd.Timedelta(hours=9, minutes=15)
        return base + pd.to_timedelta(idx % SESSION_MINUTES, unit="m")

    def frame(self, key: str, timeframe: int = 1) -> pd.DataFrame:
        """OHLCV candles for one index at 1, 5 or 15 minutes (resampled from 1m)."""
        n = self.advance()
        with self.lock:
            arr = np.array(self.candles[key][:n])
        df = pd.DataFrame(arr, columns=["open", "high", "low", "close", "volume"])
        df.insert(0, "time", self.timestamps(n))
        if timeframe == 1:
            return df
        idx = np.arange(n)
        grp = [idx // SESSION_MINUTES, (idx % SESSION_MINUTES) // timeframe]
        out = df.groupby(grp, sort=False).agg(
            time=("time", "first"), open=("open", "first"), high=("high", "max"),
            low=("low", "min"), close=("close", "last"), volume=("volume", "sum"),
        )
        return out.reset_index(drop=True)

    def _date_of_day(self, day: int) -> date:
        d = np.busday_offset(np.datetime64(self.start_date.date()), day, roll="forward")
        return d.astype(object)

    def day_index(self, d: date) -> int:
        """Virtual trading-day number of a calendar date (0 = first day)."""
        return int(np.busday_count(np.datetime64(self.start_date.date()), np.datetime64(d)))

    def snapshot(self) -> Snapshot:
        n = self.advance()
        with self.lock:
            day, k = divmod(n - 1, SESSION_MINUTES)
            return Snapshot(n=n, day=self._date_of_day(day), k=k, prices=dict(self.prices),
                            iv_atm=self.iv_atm, regime=self.regime)

    def close_at(self, key: str, idx: int) -> float | None:
        """Close of the 1-minute candle number `idx`, or None if it has not formed yet."""
        with self.lock:
            c = self.candles[key]
            return c[idx][3] if 0 <= idx < len(c) else None

    def ltp(self, key: str) -> float:
        self.advance()
        return self.prices[key]

    def prev_close(self, key: str) -> float:
        """Close of the previous trading day (or the starting level on day 1)."""
        n = self.advance()
        day_start = ((n - 1) // SESSION_MINUTES) * SESSION_MINUTES
        if day_start == 0:
            return INDICES[key]["start"]
        return self.candles[key][day_start - 1][3]
