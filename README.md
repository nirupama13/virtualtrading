# Virtual Market

A fully simulated stock market (Nifty, Bank Nifty, Sensex) for students to practise trading with virtual money.
It has **no connection to live market data**.

## Run locally

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pytest
streamlit run app.py
```

Admin panel (sidebar): default password `admin123`. Change it with the `ADMIN_PASSWORD` environment variable
or `.streamlit/secrets.toml` (`ADMIN_PASSWORD = "..."`).

## Deploy (free) on Streamlit Community Cloud
1. Push this repo to GitHub (public).
2. Sign in at https://share.streamlit.io with GitHub -> **Create app** -> pick this repo, branch `main`, file `app.py`.
3. In **Advanced settings -> Secrets** add: `ADMIN_PASSWORD = "your-password"`.
4. Deploy. Note: the market and accounts live in server memory, so they reset when the app sleeps or redeploys.

## Roadmap
1. Price engine + interactive chart (this phase)
2. Option chain (Black-Scholes, Greeks, expiries)
3. Orders, positions, P&L (buy/sell, option selling, hedging)
4. Student login + leaderboard
5. Polish + deployment
