"""Admin-controlled settings shared by every student session in the running app."""
from __future__ import annotations

import threading

from .market import Market

WARMUP_MINUTES = 750  # two sessions of history so charts are not empty at start


class Hub:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.same_market = True      # True: all students see one market (default)
        self.scenario = "auto"       # "auto" or bullish/bearish/sideways/volatile
        self.speed = 1.0             # 1.0 = one candle per real minute
        self.epoch = 0               # bump to force a fresh market
        self._shared: Market | None = None
        self.accounts: dict = {}     # market seed -> Account (single-user for now; login comes later)

    def _new_market(self) -> Market:
        m = Market(scenario=self.scenario, speed=self.speed, warmup=WARMUP_MINUTES)
        m.epoch = self.epoch
        return m

    def shared_market(self) -> Market:
        with self.lock:
            if self._shared is None or self._shared.epoch != self.epoch:
                self._shared = self._new_market()
            return self._shared

    def personal_market(self, current: Market | None) -> Market:
        """Market for a single student session (used when same_market is False)."""
        with self.lock:
            if current is None or current.epoch != self.epoch:
                current = self._new_market()
            return current

    def sync(self, m: Market) -> Market:
        """Apply admin scenario/speed changes to a market."""
        if m.scenario != self.scenario:
            m.set_scenario(self.scenario)
        if m.speed != self.speed:
            m.set_speed(self.speed)
        return m

    def account_for(self, m: Market):
        """The virtual-money account tied to a market. A new market means a fresh account."""
        from trading.broker import Account
        with self.lock:
            return self.accounts.setdefault(m.seed, Account())

    def reset(self) -> None:
        with self.lock:
            self.epoch += 1
