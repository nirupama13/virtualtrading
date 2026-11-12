"""Option chain + trade ticket (stacked, for the right-hand column)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engine.market import INDICES
from engine.options import bid_ask, expiry_dates, minutes_to_expiry, option_chain, quote
from trading import broker

ITM_BG = "background-color: rgba(41,98,255,0.09)"
ATM_BG = "background-color: rgba(245,158,11,0.25); font-weight: 700"
COLS = ["CE OI", "CE Δ", "CE LTP", "Strike", "PE LTP", "PE Δ", "PE OI"]


def _style(df: pd.DataFrame, spot: float, atm: int):
    def row_style(row):
        out = [""] * len(row)
        for i, col in enumerate(row.index):
            if col == "Strike" and row["Strike"] == atm:
                out[i] = ATM_BG
            elif col.startswith("CE") and row["Strike"] < spot:
                out[i] = ITM_BG
            elif col.startswith("PE") and row["Strike"] > spot:
                out[i] = ITM_BG
        return out

    fmt = {c: "{:,.0f}" for c in df.columns if c.endswith("OI")}
    fmt.update({c: "{:.2f}" for c in df.columns if c.endswith(("LTP", "Δ"))})
    fmt["Strike"] = "{:,.0f}"
    return df.style.apply(row_style, axis=1).format(fmt)


def _ticket(m, acct, symbol: str, expiry, strikes: list[int], atm: int) -> None:
    cfg = INDICES[symbol]
    st.markdown("**Trade ticket**")
    sk = f"strike_{symbol}"
    if sk not in st.session_state or st.session_state[sk] not in strikes:
        st.session_state[sk] = atm
    a, b, c = st.columns([1, 1.4, 1])
    kind = a.radio("Type", ["CE", "PE"], horizontal=True, key=f"kind_{symbol}")
    strike = b.selectbox("Strike", strikes, key=sk)
    lots = c.number_input(f"Lots ×{cfg['lot']}", min_value=1, max_value=1000, value=1, step=1, key=f"lots_{symbol}")
    d, e = st.columns([1.4, 1])
    order_type = d.radio("Order", ["MARKET", "LIMIT"], horizontal=True, key=f"ot_{symbol}")

    snap = m.snapshot()
    q = quote(snap, symbol, expiry, strike, kind)
    bid, ask = bid_ask(q["ltp"])
    limit = None
    if order_type == "LIMIT":
        limit = e.number_input("Limit price", min_value=0.05, value=float(q["ltp"]), step=0.05, format="%.2f",
                               key=f"limit_{symbol}")
    st.caption(f"**{symbol} {strike} {kind}** · LTP **{q['ltp']:.2f}** · Bid {bid:.2f} / Ask {ask:.2f} · "
               f"IV {q['iv'] * 100:.1f}% · Δ {q['delta']:.2f} · Θ {q['theta']:.2f}/day")

    s = broker.summary(acct, snap)
    b1, b2 = st.columns(2)
    for col, side, icon in ((b1, "BUY", "🟢"), (b2, "SELL", "🔴")):
        if col.button(f"{icon} {side}", key=f"{side}_{symbol}", width="stretch",
                      type="primary" if side == "BUY" else "secondary"):
            ok, msg = broker.place_order(acct, m, symbol, expiry, int(strike), kind, side, int(lots), order_type, limit)
            st.session_state["flash"] = (msg, "✅" if ok else "⚠️")
            st.rerun()
        ev = broker.preview_order(acct, m, symbol, expiry, int(strike), kind, side, int(lots))
        col.caption(f"Margin after ₹{ev['margin_after']:,.0f}" if ev.get("ok") else f"⚠️ {ev['msg']}")
    st.caption(f"Available funds ₹{s['available']:,.0f}")


@st.fragment(run_every="5s")
def render(market_getter, account_getter, symbol: str) -> None:
    m, acct = market_getter(), account_getter()
    for note in broker.process_pending(acct, m):
        st.toast(note, icon="✅")
    snap = m.snapshot()
    cfg = INDICES[symbol]
    spot = snap.prices[symbol]

    expiries = expiry_dates(symbol, snap.day)
    t1, t2 = st.columns([2, 1], vertical_alignment="bottom")
    expiry = t1.selectbox("Expiry", expiries, format_func=lambda d: f"{d:%d %b %Y} ({d:%a})", key=f"exp_{symbol}")
    n_strikes = t2.number_input("Strikes ±", 3, 25, 7, key="n_strikes")
    days = minutes_to_expiry(snap, expiry) / 375

    df = option_chain(snap, m.seed, symbol, expiry, int(n_strikes))
    atm = int(round(spot / cfg["step"]) * cfg["step"])
    pcr = df["PE OI"].sum() / max(df["CE OI"].sum(), 1)
    st.caption(f"**{cfg['name']} {spot:,.2f}** · ATM IV {snap.iv_atm * 100:.1f}% · {days:.1f} day(s) to expiry · "
               f"PCR {pcr:.2f} · OI is simulated")
    st.dataframe(_style(df[COLS], spot, atm), hide_index=True, width="stretch",
                 height=min(35 * len(df) + 38, 420))
    _ticket(m, acct, symbol, expiry, [int(k) for k in df["Strike"]], atm)
