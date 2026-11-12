import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from engine.market import INDICES, Market


def _fill(m: Market, minutes: int) -> None:
    m._virt_seconds = minutes * 60
    m.advance()


def test_same_seed_same_market():
    a, b = Market(seed=1), Market(seed=1)
    _fill(a, 400)
    _fill(b, 400)
    assert a.frame("NIFTY").equals(b.frame("NIFTY"))


def test_ohlc_valid():
    m = Market(seed=2)
    _fill(m, 800)
    for key in INDICES:
        df = m.frame(key)
        assert (df.high >= df[["open", "close"]].max(axis=1)).all()
        assert (df.low <= df[["open", "close"]].min(axis=1)).all()
        assert (df.close > 0).all()


def test_resample_consistent():
    m = Market(seed=3)
    _fill(m, 749)  # first candle is immediate -> exactly 750 candles = 2 sessions
    one, five, fifteen = (m.frame("BANKNIFTY", t) for t in (1, 5, 15))
    assert len(five) == 750 // 5 and len(fifteen) == 750 // 15
    assert five.volume.sum() == one.volume.sum()
    assert five.high.max() == one.high.max() and fifteen.low.min() == one.low.min()
    assert five.close.iloc[-1] == one.close.iloc[-1]


def test_scenarios_behave():
    def move(scn):
        rets = []
        for seed in range(15):
            m = Market(seed=seed, scenario=scn)
            _fill(m, 300)
            c = m.frame("NIFTY").close
            rets.append((c.iloc[-1] / c.iloc[0] - 1, np.log(c).diff().std()))
        return np.mean([r[0] for r in rets]), np.mean([r[1] for r in rets])

    assert move("bullish")[0] > 0 > move("bearish")[0]
    assert move("volatile")[1] > 2 * move("sideways")[1]
    assert abs(move("sideways")[0]) < abs(move("bullish")[0])
