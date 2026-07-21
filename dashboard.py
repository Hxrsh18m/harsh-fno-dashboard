"""
Live strategy screener dashboard (Streamlit).

Usage:  pip install streamlit yfinance pandas numpy
        streamlit run dashboard.py
Needs:  ACL_HARSH.csv  (symbol universe, column "NSE_Symbol")
        ./data/*.csv    (OHLCV history from yahoo_data_downloader.py, incl. NIFTY.csv)

Data source:
  History is read from the local ./data folder (refreshed by yahoo_data_downloader.py).
  In LIVE mode the app additionally pulls the last few days from Yahoo (best-effort;
  if the network call fails it silently falls back to the local candles).

Screens all symbols against the strategy:
  Universe : market cap >= Rs 40,000 cr only (curated in market_cap.py)
  CE : EMA5>EMA43, Close>EMA200, 60<RSI(W)<80, 60<RSI(M)<80,
       Close NOT above upper Bollinger(20,2), Vol>20D avg, Nifty>50EMA, ADX filter
  PE : EMA5<EMA43, Close<EMA200, RSI(W)<40, RSI(M)<50,
       Close NOT below lower Bollinger(20,2), Vol>20D avg, Nifty<50EMA, ADX filter
Modes:
  Confirmed = last completed daily candle (matches backtest)
  Live      = latest (forming) candle (indicative, Yahoo ~15min delayed)
"""

import os, glob
from datetime import datetime
import numpy as np
import pandas as pd
import streamlit as st
from market_cap import is_large_cap

SYMBOLS_FILE = "ACL_HARSH.csv"
DATA_DIR     = "data"
NIFTY_FILE   = os.path.join(DATA_DIR, "NIFTY.csv")
RSI_LEN, ADX_LEN = 14, 14
BB_LEN, BB_STD   = 20, 2.0

# ---------------- indicators ----------------
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(close, n=RSI_LEN):
    d  = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))

def adx(df, n=ADX_LEN):
    h, l, c = df["High"], df["Low"], df["Close"]
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean().replace(0, np.nan)
    pdi = 100 * pdm.ewm(alpha=1/n, adjust=False).mean() / atr
    mdi = 100 * mdm.ewm(alpha=1/n, adjust=False).mean() / atr
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()

def htf_rsi_last(close, rule, live):
    """RSI of the latest higher-timeframe bar (forming if live, else last completed)."""
    bars = close.resample(rule).last().dropna()
    r = rsi(bars)
    if len(r) < 2:
        return np.nan
    return r.iloc[-1] if live else r.iloc[-2]   # forming vs completed period

# ---------------- data ----------------
def load_csv(path):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["Date"], index_col="Date").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(df.columns):
        return None
    return df.dropna(subset=["Open", "High", "Low", "Close"])

def load_symbols(path=SYMBOLS_FILE):
    """Read the symbol universe, tolerant of a 'NSE_Symbol' header or a bare list."""
    raw = pd.read_csv(path, header=None)
    col = raw[0].dropna().astype(str).str.strip()
    if col.iloc[0].lower() in ("nse_symbol", "symbol"):
        col = col.iloc[1:]
    return [s for s in col.tolist() if s]

def _live_overlay(out, nifty):
    """Best-effort: pull the last few days from Yahoo and merge on top of local candles.
    Any failure (offline, rate-limit, bad response) leaves the local data untouched."""
    try:
        import yfinance as yf
        tickers = [f"{s}.NS" for s in out] + ["^NSEI"]
        raw = yf.download(tickers, period="5d", interval="1d", auto_adjust=False,
                          group_by="ticker", progress=False, threads=True)
        if raw is None or raw.empty or not isinstance(raw.columns, pd.MultiIndex):
            return out, nifty
        cols = ["Open", "High", "Low", "Close", "Volume"]

        def merge(df, tkr):
            if df is None or tkr not in raw.columns.get_level_values(0):
                return df
            recent = raw[tkr].dropna(subset=["Close"])
            recent = recent[[c for c in cols if c in recent.columns]]
            if recent.empty:
                return df
            m = pd.concat([df[cols], recent])
            m = m[~m.index.duplicated(keep="last")].sort_index()
            return m

        out = {s: merge(df, f"{s}.NS") for s, df in out.items()}
        nifty = merge(nifty, "^NSEI")
    except Exception:
        pass
    return out, nifty

@st.cache_data(ttl=600, show_spinner=False)
def fetch_all(symbols, live):
    out = {}
    for s in symbols:
        if not is_large_cap(s):        # market cap >= Rs 40,000 cr gate
            continue
        df = load_csv(os.path.join(DATA_DIR, f"{s}.csv"))
        if df is not None and len(df) > 300:
            out[s] = df
    nifty = load_csv(NIFTY_FILE)
    if live:
        out, nifty = _live_overlay(out, nifty)
    return out, nifty

# ---------------- screening ----------------
def screen(df, nifty_bull, live):
    d = df if live else df.iloc[:-1]
    if len(d) < 260:
        return None
    c = d["Close"]
    e5, e43, e200 = ema(c, 5).iloc[-1], ema(c, 43).iloc[-1], ema(c, 200).iloc[-1]
    px = c.iloc[-1]
    a  = adx(d)
    adx_v, adx_p, adx_p2 = a.iloc[-1], a.iloc[-2], a.iloc[-3]
    rising = bool(adx_v > adx_p)
    fell2  = bool((adx_v < adx_p) and (adx_p < adx_p2))
    adx_ok = bool((adx_v > 25 and not fell2) or (20 <= adx_v <= 25 and rising))
    vol_ok = bool(d["Volume"].iloc[-1] > d["Volume"].rolling(20).mean().iloc[-1])
    rw = htf_rsi_last(c, "W-FRI", live)
    rm = htf_rsi_last(c, "ME", live)
    # Bollinger Band(20, 2) on daily close
    bb_mid = c.rolling(BB_LEN).mean().iloc[-1]
    bb_sd  = c.rolling(BB_LEN).std().iloc[-1]
    bb_up, bb_lo = bb_mid + BB_STD * bb_sd, bb_mid - BB_STD * bb_sd

    row = dict(price=round(px, 2), ema5=round(e5, 1), ema43=round(e43, 1),
               ema200=round(e200, 1), rsi_w=round(rw, 1), rsi_m=round(rm, 1),
               adx=round(adx_v, 1), adx_rising=rising, vol_ok=vol_ok)

    trend_up = bool(e5 > e43 and px > e200)
    trend_dn = bool(e5 < e43 and px < e200)
    ce_ok = bool(60 < rw < 80)      # NaN-safe: any comparison with NaN -> False
    cm_ok = bool(60 < rm < 80)
    pe_ok = bool(rw < 40)
    pm_ok = bool(rm < 50)
    bb_up_ok = bool(px <= bb_up)    # not above upper band
    bb_lo_ok = bool(px >= bb_lo)    # not below lower band
    row["bb_ok"] = bb_up_ok if nifty_bull else bb_lo_ok
    row["CE"] = bool(trend_up and ce_ok and cm_ok and bb_up_ok and vol_ok and adx_ok and nifty_bull)
    row["PE"] = bool(trend_dn and pe_ok and pm_ok and bb_lo_ok and vol_ok and adx_ok and not nifty_bull)
    # near-miss: trend + regime ok, one RSI/BB/vol/ADX condition short
    conds_ce = [trend_up, ce_ok, cm_ok, bb_up_ok, vol_ok, adx_ok, nifty_bull]
    conds_pe = [trend_dn, pe_ok, pm_ok, bb_lo_ok, vol_ok, adx_ok, not nifty_bull]
    row["ce_score"], row["pe_score"] = int(sum(conds_ce)), int(sum(conds_pe))
    return row

def build_table(data, nifty_bull, live):
    """Screen all symbols and return a properly-typed DataFrame (empty if none)."""
    rows = {s: r for s, df in data.items() if (r := screen(df, nifty_bull, live))}
    if not rows:
        return pd.DataFrame()
    tab = pd.DataFrame(rows).T
    tab.index.name = "Symbol"
    # pd.DataFrame(...).T yields all-object columns -> cast so masks/sorts work
    bool_cols = ["CE", "PE", "adx_rising", "vol_ok", "bb_ok"]
    for col in bool_cols:
        tab[col] = tab[col].astype(bool)
    num_cols = [c for c in tab.columns if c not in bool_cols]
    tab[num_cols] = tab[num_cols].apply(pd.to_numeric, errors="coerce")
    return tab

def nifty_regime(nifty, live):
    nd = nifty if live else nifty.iloc[:-1]
    px  = nd["Close"].iloc[-1]
    e50 = ema(nd["Close"], 50).iloc[-1]
    return float(px), float(e50), bool(px > e50)

# ---------------- UI ----------------
CSS = """
<style>
  #MainMenu, footer, header {visibility: hidden;}
  [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none;}
  .block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px;}
  .hero {
     background: linear-gradient(120deg, #0f2a1a 0%, #10243a 55%, #1a1030 100%);
     border: 1px solid #223; border-radius: 16px; padding: 20px 26px; margin-bottom: 14px;
  }
  .hero h1 {margin: 0; font-size: 30px; letter-spacing: .5px; color: #F3F5F8;}
  .hero p  {margin: 4px 0 0; color: #9aa4b2; font-size: 14px;}
  .pill {display:inline-block; padding:4px 12px; border-radius:999px; font-weight:700;
         font-size:13px; letter-spacing:.4px;}
  .pill-bull {background:rgba(0,200,5,.15); color:#33e06a; border:1px solid rgba(0,200,5,.4);}
  .pill-bear {background:rgba(255,64,64,.15); color:#ff6b6b; border:1px solid rgba(255,64,64,.4);}
  [data-testid="stMetric"] {
     background:#141924; border:1px solid #222a38; border-radius:14px;
     padding:14px 18px;
  }
  [data-testid="stMetricLabel"] p {color:#8b95a5 !important; font-size:12px !important;
     text-transform:uppercase; letter-spacing:.6px;}
  [data-testid="stMetricValue"] {font-size:26px !important;}
  .stTabs [data-baseweb="tab-list"] {gap: 6px;}
  .stTabs [data-baseweb="tab"] {background:#141924; border-radius:10px 10px 0 0; padding:6px 16px;}
  .stTabs [aria-selected="true"] {background:#1d2534;}
</style>
"""

COLCFG = None  # built lazily inside main() (needs st runtime)

def _colcfg():
    return {
        "price":      st.column_config.NumberColumn("Price", format="₹%.2f"),
        "ema5":       st.column_config.NumberColumn("EMA 5", format="%.1f"),
        "ema43":      st.column_config.NumberColumn("EMA 43", format="%.1f"),
        "ema200":     st.column_config.NumberColumn("EMA 200", format="%.1f"),
        "rsi_w":      st.column_config.ProgressColumn("RSI · W", min_value=0, max_value=100, format="%.0f"),
        "rsi_m":      st.column_config.ProgressColumn("RSI · M", min_value=0, max_value=100, format="%.0f"),
        "adx":        st.column_config.ProgressColumn("ADX", min_value=0, max_value=60, format="%.0f"),
        "adx_rising": st.column_config.CheckboxColumn("ADX ↑"),
        "vol_ok":     st.column_config.CheckboxColumn("Vol>20D"),
        "bb_ok":      st.column_config.CheckboxColumn("In BB"),
        "ce_score":   st.column_config.NumberColumn("Score", format="%d /7"),
        "pe_score":   st.column_config.NumberColumn("Score", format="%d /7"),
        "CE":         st.column_config.CheckboxColumn("CE"),
        "PE":         st.column_config.CheckboxColumn("PE"),
    }

def show_table(df, cols, empty_msg="No symbols match right now."):
    if df is None or not len(df):
        st.info(empty_msg)
        return
    d = df[cols].copy()
    for c in ("rsi_w", "rsi_m"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce").clip(0, 100).fillna(0)
    if "adx" in d:
        d["adx"] = pd.to_numeric(d["adx"], errors="coerce").clip(0, 60).fillna(0)
    st.dataframe(d, width="stretch",
                 column_config={k: v for k, v in _colcfg().items() if k in cols})

def main():
    st.set_page_config(page_title="ATF Strategy Screener", page_icon="⚡",
                       layout="wide", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="hero"><h1>⚡ ATF Strategy Screener</h1>'
        '<p>NSE F&amp;O · mktcap ≥ ₹40,000cr · EMA 5/43/200 · RSI W&amp;M bands · Bollinger · ADX · Nifty regime</p>'
        '</div>', unsafe_allow_html=True)

    # ---- controls ----
    c1, c2, c3 = st.columns([2, 2, 8])
    live = c1.toggle("🔴 Live mode", value=False,
                     help="OFF = last completed daily candle (matches backtest). "
                          "ON = latest forming candle + best-effort Yahoo refresh.")
    if c2.button("↻ Refresh", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    if not os.path.exists(SYMBOLS_FILE):
        st.error(f"Symbol file '{SYMBOLS_FILE}' not found.")
        st.stop()
    symbols = load_symbols()

    with st.spinner(f"Loading {len(symbols)} symbols…"):
        data, nifty = fetch_all(symbols, live)

    if nifty is None or nifty.empty:
        st.error(f"NIFTY history not found — expected {NIFTY_FILE}. Run yahoo_data_downloader.py first.")
        st.stop()
    if not data:
        st.error("No symbol data loaded from ./data. Run yahoo_data_downloader.py first.")
        st.stop()

    nifty_px, nifty_e50, nifty_bull = nifty_regime(nifty, live)
    tab = build_table(data, nifty_bull, live)
    if tab.empty:
        st.warning("No symbols passed the minimum-history filter.")
        st.stop()

    ce = tab[tab["CE"]].sort_values("adx", ascending=False)
    pe = tab[tab["PE"]].sort_values("adx", ascending=False)
    side = "ce_score" if nifty_bull else "pe_score"
    near = tab[~tab["CE"] & ~tab["PE"] & (tab[side] == 6)].sort_values("adx", ascending=False)

    # ---- regime badge ----
    badge = ('<span class="pill pill-bull">▲ BULLISH · CE side</span>' if nifty_bull
             else '<span class="pill pill-bear">▼ BEARISH · PE side</span>')
    c3.markdown(f"<div style='padding-top:6px'>{badge}</div>", unsafe_allow_html=True)

    # ---- KPI row ----
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("NIFTY 50", f"{nifty_px:,.0f}", f"{(nifty_px/nifty_e50-1)*100:+.2f}% vs 50EMA")
    k2.metric("CE candidates", len(ce))
    k3.metric("PE candidates", len(pe))
    k4.metric("Near-miss (6/7)", len(near))
    data_dt = nifty.index[-1] if live else nifty.index[-2]
    k5.metric("Data as of", data_dt.strftime("%d %b %Y"))

    mode = "LIVE · forming candle" if live else "CONFIRMED · last close"
    st.caption(f"Mode: **{mode}**  ·  {len(tab)} symbols screened  ·  "
               f"history from ./data  ·  refreshed {datetime.now().strftime('%H:%M:%S')}  ·  "
               "Live overlay via Yahoo (~15 min delayed)")

    # ---- tabbed candidate views ----
    show = ["price", "ema5", "ema43", "ema200", "rsi_w", "rsi_m", "adx", "adx_rising", "vol_ok", "bb_ok"]
    t_ce, t_pe, t_near, t_all = st.tabs([
        f"🟢  CE candidates · {len(ce)}",
        f"🔴  PE candidates · {len(pe)}",
        f"🟡  Near-miss · {len(near)}",
        f"📋  All symbols · {len(tab)}",
    ])
    with t_ce:
        st.caption("EMA5 > EMA43 · Close > EMA200 · 60 < RSI(W) < 80 · 60 < RSI(M) < 80 · not above upper BB · Vol > 20D · ADX · Nifty bullish")
        show_table(ce, show, "No CE signals on the last candle.")
    with t_pe:
        st.caption("EMA5 < EMA43 · Close < EMA200 · RSI(W) < 40 · RSI(M) < 50 · not below lower BB · Vol > 20D · ADX · Nifty bearish")
        show_table(pe, show, "No PE signals on the last candle.")
    with t_near:
        st.caption(f"6 of 7 conditions met on the {'CE' if nifty_bull else 'PE'} side — one trigger away.")
        show_table(near, show + [side], "No near-miss setups.")
    with t_all:
        st.caption("Full screen — every symbol with its indicator snapshot.")
        show_table(tab.sort_values("adx", ascending=False),
                   show + ["CE", "PE", "ce_score", "pe_score"])

if __name__ == "__main__":
    main()
