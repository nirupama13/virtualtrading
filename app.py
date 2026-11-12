"""Virtual Market - synthetic index market with option chain and paper trading (single page).

Run:  streamlit run app.py   (or .\\run.ps1)
"""
from __future__ import annotations

import os

import streamlit as st

from engine.hub import Hub
from engine.market import INDICES, SCENARIOS
from views import chain, chart, portfolio

st.set_page_config(page_title="Virtual Market", page_icon="📈", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
      [data-testid="stAppDeployButton"], [data-testid="stSidebarCollapsedControl"], footer {display: none !important;}
      .block-container {padding-top: 2.6rem; padding-bottom: 1rem; max-width: 100%;}
      [data-testid="stMetricValue"] {font-size: 1.15rem;}
      [data-testid="stMetricLabel"] p {font-size: 0.75rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15}


@st.cache_resource
def get_hub() -> Hub:
    return Hub()


hub = get_hub()


def get_market():
    if hub.same_market:
        return hub.sync(hub.shared_market())
    m = hub.personal_market(st.session_state.get("market"))
    st.session_state["market"] = m
    return hub.sync(m)


def get_account():
    return hub.account_for(get_market())


def admin_password() -> str:
    try:
        return st.secrets["ADMIN_PASSWORD"]
    except Exception:
        return os.environ.get("ADMIN_PASSWORD", "admin123")


if flash := st.session_state.pop("flash", None):
    st.toast(flash[0], icon=flash[1])

# ---------------- top bar ----------------
h_title, h_index, h_tf, h_admin = st.columns([2.2, 2, 2, 1.2], vertical_alignment="center")
h_title.markdown("### 📈 Virtual Market")
symbol = h_index.selectbox("Index", list(INDICES), format_func=lambda k: INDICES[k]["name"],
                           label_visibility="collapsed")
tf_label = h_tf.segmented_control("Timeframe", list(TIMEFRAMES), default="1m", label_visibility="collapsed") or "1m"

with h_admin.popover("⚙️ Admin", width="stretch"):
    pwd = st.text_input("Password", type="password")
    if pwd and pwd == admin_password():
        st.session_state["is_admin"] = True
    if st.session_state.get("is_admin"):
        hub.same_market = st.toggle(
            "Same market for all students", value=hub.same_market,
            help="Off: every student gets their own independent market.",
        )
        hub.scenario = st.selectbox(
            "Scenario", SCENARIOS, index=SCENARIOS.index(hub.scenario),
            help="auto = regimes change randomly over time.",
        )
        hub.speed = st.number_input(
            "Speed (1 = real time)", min_value=0.0, max_value=600.0, value=float(hub.speed), step=1.0,
            help="1 = one candle per real minute. Raise it only to test or demo.",
        )
        if st.button("Reset market"):
            hub.reset()
            st.rerun()
    elif pwd:
        st.error("Wrong password")

# ---------------- one page ----------------
left, right = st.columns([1.55, 1], gap="medium")
with left:
    chart.render(get_market, symbol, TIMEFRAMES[tf_label], tf_label)
    portfolio.render(get_market, get_account)
with right:
    chain.render(get_market, get_account, symbol)
