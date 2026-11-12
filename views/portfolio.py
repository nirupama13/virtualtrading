"""Positions, orders, trade book and P&L (compact, sits under the chart)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from trading import broker


def _money(x: float) -> str:
    return f"₹{x:,.0f}"


@st.fragment(run_every="5s")
def render(market_getter, account_getter) -> None:
    m, acct = market_getter(), account_getter()
    for note in broker.process_pending(acct, m):
        st.toast(note, icon="✅")
    snap = m.snapshot()
    s = broker.summary(acct, snap)

    c = st.columns(6)
    c[0].metric("Equity", _money(s["equity"]), f"{s['pnl']:+,.0f} ({s['pnl'] / acct.capital * 100:+.2f}%)")
    c[1].metric("Cash", _money(s["cash"]))
    c[2].metric("Margin blocked", _money(s["margin"]))
    c[3].metric("Available", _money(s["available"]))
    c[4].metric("Realized P&L", f"{s['realized']:+,.0f}")
    c[5].metric("Unrealized P&L", f"{s['unrealized']:+,.0f}")

    open_orders = [o for o in acct.orders if o["status"] == "OPEN"]
    rows = broker.positions_table(acct, snap)
    t_pos, t_ord, t_book = st.tabs(["Positions", "Orders", "Trade book"])

    with t_pos:
        st.caption(f"{len(rows)} open position(s)")
        if not rows:
            st.info("No open positions. Pick a strike in the trade ticket and press BUY or SELL.")
        else:
            view = pd.DataFrame([{k: v for k, v in r.items() if k != "_key"} for r in rows])
            st.dataframe(view, hide_index=True, width="stretch", height=min(35 * len(view) + 38, 240))
            st.caption(f"Net delta {view['Delta'].sum():+,.1f} · Net theta {view['Theta/day'].sum():+,.1f} per day")
            a, b, d = st.columns([3, 1, 1])
            labels = {r["Contract"]: r["_key"] for r in rows}
            pick = a.selectbox("Position", list(labels), key="sq_pick", label_visibility="collapsed")
            if b.button("Square off", width="stretch", key="sq_one"):
                ok, msg = broker.square_off(acct, m, labels[pick])
                st.session_state["flash"] = (msg, "✅" if ok else "⚠️")
                st.rerun()
            if d.button("Square off all", width="stretch", key="sq_all"):
                st.session_state["flash"] = (broker.square_off_all(acct, m), "✅")
                st.rerun()

    with t_ord:
        st.caption(f"{len(open_orders)} open order(s)")
        if not acct.orders:
            st.caption("No orders yet.")
        else:
            view = pd.DataFrame([{
                "ID": o["id"], "Contract": broker.label(o["symbol"], o["expiry"], o["strike"], o["kind"]),
                "Side": o["side"], "Lots": o["lots"], "Limit": o["limit"], "Status": o["status"]}
                for o in reversed(acct.orders)])
            st.dataframe(view, hide_index=True, width="stretch", height=min(35 * len(view) + 38, 240))
            if open_orders:
                a, b = st.columns([3, 1])
                oid = a.selectbox("Cancel order", [o["id"] for o in open_orders], key="cancel_pick",
                                  label_visibility="collapsed")
                if b.button("Cancel", width="stretch", key="cancel_btn"):
                    broker.cancel_order(acct, oid)
                    st.rerun()

    with t_book:
        if acct.trades:
            st.dataframe(pd.DataFrame(reversed(acct.trades)), hide_index=True, width="stretch", height=240)
        else:
            st.caption("No trades yet.")
        with st.expander("Account"):
            st.caption("Resets cash to ₹10,00,000 and clears all positions, orders and trades.")
            if st.button("Reset my account", key="reset_acct"):
                fresh = broker.Account()
                acct.__dict__.update({k: v for k, v in fresh.__dict__.items() if k != "lock"})
                st.rerun(scope="fragment")
