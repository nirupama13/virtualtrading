"""TradingView-style candlestick chart (light theme)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine.market import INDICES

BG, GRID, TEXT, AXIS = "#faf8f3", "#ebe8de", "#6b6e78", "#d5d2c6"
UP, DOWN = "#089981", "#f23645"
EMA_COLORS = {9: "#f59e0b", 21: "#2962ff"}


def build_figure(df: pd.DataFrame, title: str, shown: int, follow: bool, tf_label: str, emas: list[int]) -> go.Figure:
    last = df.iloc[-1]
    price_color = UP if last.close >= last.open else DOWN
    view = df.tail(shown)
    lo, hi = view.low.min(), view.high.max()
    pad = (hi - lo) * 0.08 or 1.0

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df.time, open=df.open, high=df.high, low=df.low, close=df.close, name=title,
        increasing=dict(line=dict(color=UP, width=1), fillcolor=UP),
        decreasing=dict(line=dict(color=DOWN, width=1), fillcolor=DOWN),
        whiskerwidth=0, yaxis="y",
    ))
    for n in emas:
        fig.add_trace(go.Scatter(x=df.time, y=df.close.ewm(span=n, adjust=False).mean(), mode="lines",
                                 line=dict(color=EMA_COLORS[n], width=1.3), name=f"EMA {n}", hoverinfo="skip"))
    vmax = view.volume.max() or 1
    fig.add_trace(go.Bar(
        x=df.time, y=df.volume, yaxis="y2", hoverinfo="skip", name="Volume",
        marker=dict(color=[UP if c >= o else DOWN for o, c in zip(df.open, df.close)], opacity=0.3),
    ))

    fig.add_hline(y=last.close, line=dict(color=price_color, width=1, dash="dot"))
    fig.add_annotation(x=1, xref="paper", y=last.close, yref="y", text=f"{last.close:,.2f}", showarrow=False,
                       xanchor="left", font=dict(color="white", size=12), bgcolor=price_color, borderpad=3)
    legend = (f"<b>{title}</b> · {tf_label} &nbsp; "
              f"<span style='color:{price_color}'>O {last.open:,.2f} &nbsp;H {last.high:,.2f} &nbsp;"
              f"L {last.low:,.2f} &nbsp;C {last.close:,.2f}</span>")
    fig.add_annotation(x=0, xref="paper", y=1, yref="paper", text=legend, showarrow=False,
                       xanchor="left", yanchor="bottom", font=dict(color="#1f2430", size=12))

    step = view.time.diff().median() if len(view) > 1 else pd.Timedelta(minutes=1)
    x_range = [view.time.iloc[0] - step, view.time.iloc[-1] + step * max(3, shown // 8)] if follow else None

    fig.update_layout(
        height=470, margin=dict(l=8, r=64, t=30, b=8), paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT, size=11), showlegend=False, dragmode="pan", hovermode="x",
        hoverlabel=dict(bgcolor="white", bordercolor=AXIS, font=dict(color="#1f2430", size=12)),
        xaxis=dict(
            rangeslider=dict(visible=False), showgrid=True, gridcolor=GRID, linecolor=AXIS,
            showspikes=True, spikemode="across", spikesnap="cursor", spikedash="dash", spikecolor="#9598a1",
            spikethickness=1, range=x_range,
            rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[15.5, 9.25], pattern="hour")],
        ),
        yaxis=dict(
            side="right", showgrid=True, gridcolor=GRID, zeroline=False, linecolor=AXIS, tickformat=",.0f",
            showspikes=True, spikemode="across", spikedash="dash", spikecolor="#9598a1", spikethickness=1,
            range=[lo - pad, hi + pad] if follow else None, fixedrange=False,
        ),
        yaxis2=dict(overlaying="y", side="left", showgrid=False, visible=False, range=[0, vmax * 5]),
        uirevision=f"{title}-{tf_label}-{len(df) if follow else 'free'}",
    )
    return fig


@st.fragment(run_every="5s")
def render(market_getter, symbol: str, timeframe: int, tf_label: str) -> None:
    m = market_getter()
    cfg = INDICES[symbol]
    c1, c2, c3, c4, c5 = st.columns([1.1, 0.8, 0.8, 1, 2.6], vertical_alignment="center")
    follow = c1.toggle("Follow live", value=True, key="chart_follow")
    emas = [n for n, c in ((9, c2), (21, c3)) if c.checkbox(f"EMA {n}", key=f"ema{n}")]
    shown = c4.selectbox("Candles", [50, 100, 150, 250], index=1, key="candles_shown", label_visibility="collapsed")

    df = m.frame(symbol, timeframe).tail(1500)
    ltp, prev = m.ltp(symbol), m.prev_close(symbol)
    chg = ltp - prev
    color = UP if chg >= 0 else DOWN
    c5.markdown(
        f"<div style='text-align:right;font-size:1.4rem;font-weight:600'>{ltp:,.2f} "
        f"<span style='color:{color};font-size:0.95rem'>{chg:+,.2f} ({chg / prev * 100:+.2f}%)</span></div>",
        unsafe_allow_html=True)
    if st.session_state.get("is_admin"):
        st.caption(f"Admin only - regime: **{m.regime_log[-1]}** · scenario: **{m.scenario}** · "
                   f"ATM IV: {m.iv_atm * 100:.1f}% · seed {m.seed}")

    fig = build_figure(df, cfg["name"], shown, follow, tf_label, emas)
    st.plotly_chart(fig, width="stretch", config=dict(
        scrollZoom=True, displaylogo=False, doubleClick="reset",
        modeBarButtonsToRemove=["select2d", "lasso2d", "autoScale2d", "toImage"]))
