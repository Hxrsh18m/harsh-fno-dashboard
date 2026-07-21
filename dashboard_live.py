"""
====================================================================
  HARSH FnO API 1.0  -  live F&O strategy scanner (Streamlit)
====================================================================
Scans NSE F&O stocks live, applies the multi-timeframe strategy
(daily side filter + 15m entry stack) and surfaces:
  * ACTIVE entry setups  -> entry, stop-loss, T1/T2/T3, R:R, option play
  * NEAR setups          -> what is still missing to trigger
  * WATCH list           -> side almost eligible
Plus a chart explorer (candles + EMAs + RSI/ADX) and a market pulse.

RUN:  streamlit run dashboard_live.py
Data: Yahoo Finance (delayed ~15m). Educational use only - not advice.
====================================================================
"""
import math
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit.components.v1 import html as st_html
import live_feed
from market_cap import is_large_cap
try:
    from streamlit_autorefresh import st_autorefresh   # soft in-place refresh (keeps UI state)
    _HAS_AUTOREFRESH = True
except Exception:
    _HAS_AUTOREFRESH = False

# ------------------------------------------------------------------ config
st.set_page_config(page_title="Harsh FnO API 1.0", page_icon="📡",
                   layout="wide", initial_sidebar_state="expanded")

# ------------------------------------------------------------------ users & access
ALL_MODULES = ["Active", "Near", "Watchlist", "Closed", "Charts", "Success", "Alerts", "Daily"]
USERS_FILE = "users.json"
SUPERADMIN = "Hxrsh"

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            return json.load(open(USERS_FILE, encoding="utf-8"))
        except Exception:
            pass
    users = {SUPERADMIN: {"password": "hxrsh18fno", "role": "superadmin", "modules": ALL_MODULES}}
    save_users(users)
    return users

def save_users(u):
    try:
        json.dump(u, open(USERS_FILE, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass

def user_modules(users, name):
    u = users.get(name, {})
    if u.get("role") == "superadmin":
        return ALL_MODULES
    return [m for m in ALL_MODULES if m in u.get("modules", [])]

# ------------------------------------------------------------------ login gate
def require_login():
    users = load_users()
    if st.session_state.get("authed"):
        return
    st.markdown('<style>[data-testid="stSidebar"]{display:none;}'
                'header,#MainMenu,footer{visibility:hidden;}</style>', unsafe_allow_html=True)
    mid = st.columns([1, 1.1, 1])[1]
    with mid:
        st.markdown("<div style='text-align:center;margin-top:9vh;'>"
                    "<h1 style='color:#00d69e;margin-bottom:0;'>📡 Harsh FnO API 1.0</h1>"
                    "<p style='color:#8b95ad;'>Please sign in to continue</p></div>",
                    unsafe_allow_html=True)
        with st.form("login_form"):
            u = st.text_input("Username")
            p = st.text_input("Password", type="password")
            ok = st.form_submit_button("Log in")
        if ok:
            rec = users.get(u)
            if rec and str(rec.get("password")) == p:
                st.session_state["authed"] = True
                st.session_state["user"] = u
                st.session_state["role"] = rec.get("role", "user")
                st.session_state["modules"] = user_modules(users, u)
                st.rerun()
            else:
                st.error("Incorrect username or password.")
        st.caption("Access is restricted to authorised users only.")
    st.stop()

require_login()
CURRENT_USER = st.session_state.get("user", SUPERADMIN)
IS_ADMIN = st.session_state.get("role") == "superadmin"
MY_MODULES = st.session_state.get("modules", ALL_MODULES)

TICKER_OVERRIDES = {
    "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MCDOWELL-N": "UNITDSPR.NS", "BIRLASOFT": "BSOFT.NS",
    "PEL": "PIRAMALFIN.NS", "TATAMOTORS": "TMCV.NS",
}
# curated liquid default universe (reliable intraday on Yahoo)
DEFAULT_UNIVERSE = [
    "RELIANCE","HDFCBANK","ICICIBANK","INFY","TCS","SBIN","AXISBANK","KOTAKBANK",
    "LT","BHARTIARTL","ITC","HINDUNILVR","BAJFINANCE","MARUTI","SUNPHARMA","TITAN",
    "ULTRACEMCO","ASIANPAINT","WIPRO","HCLTECH","TATASTEEL","JSWSTEEL","POWERGRID",
    "NTPC","ONGC","COALINDIA","ADANIENT","ADANIPORTS","GRASIM","TECHM","NESTLEIND",
    "DRREDDY","CIPLA","BAJAJFINSV","HDFCLIFE","SBILIFE","BEL","TRENT","DLF","HAL",
]

def full_universe():
    try:
        col = pd.read_csv("Options Symbols.csv", header=None)[0].dropna().astype(str).str.strip()
        col = col[col.str.upper() != "NSE_SYMBOL"]
        return [s for s in col.tolist() if s]
    except Exception:
        return DEFAULT_UNIVERSE

def map_ticker(sym):
    return TICKER_OVERRIDES.get(sym.upper(), f"{sym}.NS")

# ------------------------------------------------------------------ indicators
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + up/dn.replace(0, np.nan))

def adx(df, n=14):
    h,l,c = df["High"],df["Low"],df["Close"]
    up,dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up>dn)&(up>0),up,0.0), index=df.index)
    mdm = pd.Series(np.where((dn>up)&(dn>0),dn,0.0), index=df.index)
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    a = tr.ewm(alpha=1/n,adjust=False).mean()
    pdi = 100*pdm.ewm(alpha=1/n,adjust=False).mean()/a
    mdi = 100*mdm.ewm(alpha=1/n,adjust=False).mean()/a
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/n,adjust=False).mean()

def atr(df, n=14):
    h,l,c = df["High"],df["Low"],df["Close"]
    tr = pd.concat([h-l,(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/n,adjust=False).mean()

def _ncdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))
def bs(S,K,T,iv,call=True,r=0.065):
    if T<=0 or iv<=0 or S<=0: return max(S-K,0) if call else max(K-S,0)
    sd=iv*math.sqrt(T); d1=(math.log(S/K)+(r+0.5*iv*iv)*T)/sd; d2=d1-sd
    return S*_ncdf(d1)-K*math.exp(-r*T)*_ncdf(d2) if call else K*math.exp(-r*T)*_ncdf(-d2)-S*_ncdf(-d1)

# ------------------------------------------------------------------ data (cached)
@st.cache_data(ttl=7200, show_spinner=False)     # daily bars change once per day
def get_daily(symbols):
    yts = [map_ticker(s) for s in symbols]
    try:
        raw = yf.download(yts, period="2y", interval="1d", group_by="ticker",
                          auto_adjust=False, threads=True, progress=False)
    except Exception:
        return {}
    out = {}
    for s, yt in zip(symbols, yts):
        try:
            df = raw[yt] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df.dropna(subset=["Close"])
            if len(df) > 210: out[s] = df
        except Exception:
            continue
    return out

@st.cache_data(ttl=21600, show_spinner=False)    # one-time warm-up; live candles from Angel ticks keep it current
def get_15m(symbols):
    # native 15m, 20d — historical warm-up only; new candles are built live from Angel ticks
    yts = [map_ticker(s) for s in symbols]
    try:
        raw = yf.download(yts, period="20d", interval="15m", group_by="ticker",
                          auto_adjust=False, threads=True, progress=False)
    except Exception:
        return {}
    out = {}
    for s, yt in zip(symbols, yts):
        try:
            df = raw[yt] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if df.empty: continue
            if getattr(df.index, "tz", None) is not None:
                df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
            if len(df) > 210: out[s] = df
        except Exception:
            continue
    return out

# ------------------------------------------------------------------ live feed (singleton)
@st.cache_resource
def get_feed():
    return live_feed.LiveFeed()

def live_overlay(r, feed):
    """Overlay the latest websocket tick onto a signal row (price, %chg, proximity)."""
    lv = feed.get(r["symbol"]); long_ = r["side"] == "CE"
    if lv and lv["ltp"]:
        r["ltp"], r["price"], r["chg"], r["live"] = lv["ltp"], lv["ltp"], lv["chg"], True
    else:
        r["ltp"], r["live"] = r["price"], False
    ltp = r["ltp"]; r["live_pnl"] = (ltp - r["entry"]) / r["entry"] * 100 * (1 if long_ else -1)
    sl, t1, t2, t3 = r["sl"], r["t1"], r["t2"], r["t3"]
    if long_:
        if   ltp <= sl: r["prox_tag"], r["prox_cls"] = "⚠ SL hit", "r"
        elif ltp >= t3: r["prox_tag"], r["prox_cls"] = "🎯 T3+", "g"
        elif ltp >= t2: r["prox_tag"], r["prox_cls"] = "🎯 T2 hit", "g"
        elif ltp >= t1: r["prox_tag"], r["prox_cls"] = "🎯 T1 hit", "g"
        else:           r["prox_tag"], r["prox_cls"] = f"▲ {(t1/ltp-1)*100:.1f}% to T1", "a"
    else:
        if   ltp >= sl: r["prox_tag"], r["prox_cls"] = "⚠ SL hit", "r"
        elif ltp <= t3: r["prox_tag"], r["prox_cls"] = "🎯 T3+", "g"
        elif ltp <= t2: r["prox_tag"], r["prox_cls"] = "🎯 T2 hit", "g"
        elif ltp <= t1: r["prox_tag"], r["prox_cls"] = "🎯 T1 hit", "g"
        else:           r["prox_tag"], r["prox_cls"] = f"▼ {(1-t1/ltp)*100:.1f}% to T1", "a"
    return r

# process-global cache (survives full page reloads / new sessions) for heavy scan + backtest
@st.cache_resource
def compute_cache():
    return {}

# ------------------------------------------------------------------ evaluation
def est_iv(daily):
    dr = daily["Close"].pct_change().dropna()
    v = float(dr.tail(30).std()*math.sqrt(252)) if len(dr) > 10 else 0.25
    return min(max(v, 0.15), 0.60) if np.isfinite(v) else 0.25

def symbol_features(sym, daily, m15, nifty15_close, regime_bull):
    """HEAVY indicator snapshot (data-only, NO thresholds). Cached; unaffected by the sliders."""
    c = daily["Close"]; cc = m15["Close"]
    e50m = ema(cc, 50); e200m = ema(cc, 200); va = m15["Volume"].tail(20).mean()
    bb_mid = c.rolling(20).mean().iloc[-1]; bb_sd = c.rolling(20).std().iloc[-1]
    return {
        "e5d": float(ema(c,5).iloc[-1]), "e43d": float(ema(c,43).iloc[-1]),
        "e13d": float(ema(c,13).iloc[-1]), "e50d": float(ema(c,50).iloc[-1]), "e200d": float(ema(c,200).iloc[-1]),
        "bb_up_d": float(bb_mid + 2*bb_sd), "bb_lo_d": float(bb_mid - 2*bb_sd),
        "wk": float(rsi(c.resample("W-FRI").last().dropna()).iloc[-1]),
        "mo": float(rsi(c.resample("ME").last().dropna()).iloc[-1]),
        "vol_ok": bool(daily["Volume"].iloc[-1] > daily["Volume"].tail(20).mean()),
        "close_d": float(c.iloc[-1]), "prev_close": float(c.iloc[-2]),
        "e13": float(ema(cc,13).iloc[-1]), "e50": float(e50m.iloc[-1]), "e200": float(e200m.iloc[-1]),
        "a": float(adx(m15).iloc[-1]), "r": float(rsi(cc).iloc[-1]), "at": float(atr(m15).iloc[-1]),
        "px": float(cc.iloc[-1]),
        "two_up": bool(cc.iloc[-1] > e200m.iloc[-1] and cc.iloc[-2] > e200m.iloc[-2]),
        "two_dn": bool(cc.iloc[-1] < e200m.iloc[-1] and cc.iloc[-2] < e200m.iloc[-2]),
        "vol_ratio": float(m15["Volume"].iloc[-1] / (va if va else 1.0)),
        "rs": float(cc.pct_change(20).iloc[-1] - (nifty15_close.pct_change(20).iloc[-1] if nifty15_close is not None else 0.0)),
        "regime_bull": bool(regime_bull), "iv": est_iv(daily),
        "spark": [float(x) for x in cc.tail(40).tolist()],
    }

def row_from_features(sym, f, P):
    """CHEAP threshold logic on cached features — this is all that re-runs when a slider moves."""
    px, at = f["px"], f["at"]; vol_exp = f["vol_ratio"] > P["VOL"]
    bull = {
        "EMA5>EMA43 (D)": f["e5d"] > f["e43d"], "Close>EMA200 (D)": f["close_d"] > f["e200d"],
        "Weekly RSI 60-80": 60 < f["wk"] < 80, "Monthly RSI 60-80": 60 < f["mo"] < 80,
        "Not above Upper BB (D)": f["close_d"] <= f["bb_up_d"],
        "Vol>20D (D)": f["vol_ok"], "NIFTY>50EMA": f["regime_bull"],
        "15m stack up": px > f["e13"] > f["e50"] > f["e200"], f"15m ADX>{P['ADX']}": f["a"] > P["ADX"],
        f"15m RSI>{P['RL']}": f["r"] > P["RL"], "2 closes >EMA200": f["two_up"],
        "15m Vol surge": vol_exp, "RS > NIFTY": f["rs"] > 0,
    }
    bear = {
        "EMA5<EMA43 (D)": f["e5d"] < f["e43d"], "Close<EMA200 (D)": f["close_d"] < f["e200d"],
        "Weekly RSI<40": f["wk"] < 40, "Monthly RSI<50": f["mo"] < 50,
        "Not below Lower BB (D)": f["close_d"] >= f["bb_lo_d"],
        "Vol>20D (D)": f["vol_ok"], "NIFTY<50EMA": not f["regime_bull"],
        "15m stack dn": px < f["e13"] < f["e50"] < f["e200"], f"15m ADX>{P['ADX']}": f["a"] > P["ADX"],
        f"15m RSI<{P['RS']}": f["r"] < P["RS"], "2 closes <EMA200": f["two_dn"],
        "15m Vol surge": vol_exp, "RS < NIFTY": f["rs"] < 0,
    }
    def score(d):
        items = list(d.items()); met = [k for k,v in items if v]; miss = [k for k,v in items if not v]
        return len(met), len(items), all(v for k,v in items[:7]), sum(v for k,v in items[7:]), miss
    def build(side):
        d = bull if side == "CE" else bear
        met,total,daily_all,m15_met,miss = score(d)
        if daily_all and m15_met == 6:  status = "ACTIVE"
        elif daily_all and m15_met >= 4: status = "NEAR"
        elif met >= total-2:             status = "WATCH"
        else:                            status = None
        return dict(side=side, met=met, total=total, status=status, missing=miss)
    cand = [x for x in (build("CE"), build("PE")) if x["status"]]
    if not cand:
        return None
    rank = {"ACTIVE":3, "NEAR":2, "WATCH":1}
    best = sorted(cand, key=lambda x: (rank[x["status"]], x["met"]), reverse=True)[0]
    side = best["side"]; long_ = side == "CE"; risk = 1.5*at
    if long_:
        sl = px-risk; t1,t2,t3 = px+risk, px+2*risk, px+3*risk; ss,ls_ = px*0.975, px*0.945
    else:
        sl = px+risk; t1,t2,t3 = px-risk, px-2*risk, px-3*risk; ss,ls_ = px*1.025, px*1.055
    iv = f["iv"]
    credit = bs(px, ss, 5/365, iv, call=not long_) - bs(px, ls_, 5/365, iv, call=not long_)
    return dict(symbol=sym, side=side, status=best["status"], met=best["met"], total=best["total"],
                price=px, chg=(px/f["prev_close"]-1)*100, entry=px, sl=sl, t1=t1, t2=t2, t3=t3,
                adx=f["a"], rsi=f["r"], atr=at, rr="1:2", missing=best["missing"],
                short_k=ss, long_k=ls_, credit=max(credit,0), iv=iv, spark=f["spark"])

def evaluate(sym, daily, m15, nifty15_close, regime_bull, P):
    return row_from_features(sym, symbol_features(sym, daily, m15, nifty15_close, regime_bull), P)

# ------------------------------------------------------------------ history / signals
@st.cache_data(ttl=300, show_spinner=False)
def get_ohlc(symbol, interval, period):
    """Fetch native OHLC for one symbol at a given timeframe (for the chart)."""
    df = yf.download(map_ticker(symbol), period=period, interval=interval,
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
    return df

def daily_side_series(daily, regime_series, P):
    """Per-day CE(+1)/PE(-1)/none(0) eligibility across history."""
    c = daily["Close"]
    e5, e43, e200 = ema(c,5), ema(c,43), ema(c,200)
    wk = rsi(c.resample("W-FRI").last().dropna()).reindex(c.index, method="ffill")
    mo = rsi(c.resample("ME").last().dropna()).reindex(c.index, method="ffill")
    vol_ok = daily["Volume"] > daily["Volume"].rolling(20).mean()
    bb_mid = c.rolling(20).mean(); bb_sd = c.rolling(20).std()
    bb_up, bb_lo = bb_mid + 2*bb_sd, bb_mid - 2*bb_sd
    reg = regime_series.reindex(c.index, method="ffill") if regime_series is not None else pd.Series(1, index=c.index)
    bull = (e5>e43)&(c>e200)&(wk>60)&(wk<80)&(mo>60)&(mo<80)&(c<=bb_up)&vol_ok&(reg>0)
    bear = (e5<e43)&(c<e200)&(wk<40)&(mo<50)&(c>=bb_lo)&vol_ok&(reg<0)
    s = pd.Series(0, index=c.index); s[bull] = 1; s[bear] = -1
    return s

def entry_signals(daily, m15, nifty15_close, regime_series, P):
    """Boolean CE/PE entry-signal series over the full 15m history."""
    side_day = daily_side_series(daily, regime_series, P)
    cc = m15["Close"]
    e13, e50, e200 = ema(cc,13), ema(cc,50), ema(cc,200)
    a, r, at = adx(m15), rsi(cc), atr(m15)
    vol_exp = m15["Volume"] > P["VOL"] * m15["Volume"].rolling(20).mean()
    two_up = (cc>e200)&(cc.shift(1)>e200.shift(1)); two_dn = (cc<e200)&(cc.shift(1)<e200.shift(1))
    if nifty15_close is not None:
        nrs = nifty15_close.pct_change(20).reindex(cc.index, method="ffill")
    else:
        nrs = pd.Series(0.0, index=cc.index)
    rs = cc.pct_change(20) - nrs
    su = (cc>e13)&(e13>e50)&(e50>e200); sd = (cc<e13)&(e13<e50)&(e50<e200)
    day = pd.Series(cc.index.normalize(), index=cc.index)
    side_at = day.map(lambda d: side_day.get(d, 0))
    el = ((side_at==1) & su & (a>P["ADX"]) & (r>P["RL"]) & two_up & vol_exp & (rs>0)).fillna(False)
    es = ((side_at==-1) & sd & (a>P["ADX"]) & (r<P["RS"]) & two_dn & vol_exp & (rs<0)).fillna(False)
    return pd.DataFrame({"close":cc, "high":m15["High"], "low":m15["Low"],
                         "atr":at, "el":el, "es":es})

def last_signal(sig):
    ev = sig[sig["el"] | sig["es"]]
    if ev.empty:
        return None
    ts = ev.index[-1]; row = ev.loc[ts]
    return {"ts": ts, "side": "CE" if bool(row["el"]) else "PE", "price": float(row["close"])}

def walk_trades(sig):
    """Non-overlapping trades; outcome = did 1R target hit before the 1.5xATR stop."""
    idx = sig.index
    cl, hi, lo = sig["close"].to_numpy(), sig["high"].to_numpy(), sig["low"].to_numpy()
    at, el, es = sig["atr"].to_numpy(), sig["el"].to_numpy(), sig["es"].to_numpy()
    n, i, out = len(sig), 0, []
    while i < n:
        if el[i] or es[i]:
            lng = bool(el[i]); entry = cl[i]; risk = 1.5 * at[i]
            if not np.isfinite(risk) or risk <= 0:
                i += 1; continue
            sl = entry-risk if lng else entry+risk
            tg = entry+risk if lng else entry-risk
            res, xi = "OPEN", n-1
            for j in range(i+1, n):
                if lng:
                    if lo[j] <= sl: res, xi = "LOSS", j; break
                    if hi[j] >= tg: res, xi = "WIN", j; break
                else:
                    if hi[j] >= sl: res, xi = "LOSS", j; break
                    if lo[j] <= tg: res, xi = "WIN", j; break
            out.append({"entry_ts": idx[i], "side": "CE" if lng else "PE",
                        "entry": float(entry), "exit_ts": idx[xi], "outcome": res})
            i = xi + 1
        else:
            i += 1
    return out

def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)

def closed_15m(m15):
    """Drop the last 15m bar if it is still forming — signals confirm on CLOSE only."""
    if m15 is None or len(m15) == 0:
        return m15
    if m15.index[-1] + pd.Timedelta(minutes=15) > now_ist():
        return m15.iloc[:-1]
    return m15

def market_open():
    """True only during NSE cash hours: Mon–Fri, 09:15–15:30 IST."""
    t = now_ist()
    if t.weekday() >= 5:
        return False
    mins = t.hour * 60 + t.minute
    return (9*60 + 15) <= mins <= (15*60 + 30)

# ---- historical backtest results (per-symbol success) from backtest_strategy.py ----
BT_TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.csv")

@st.cache_data(ttl=300, show_spinner=False)
def load_backtest_scores(mtime):
    """Per-symbol (wins, losses) from the standalone daily backtest's trades.csv.
    WIN = trade closed with pnl > 0. `mtime` is part of the cache key so a fresh backtest
    (new trades.csv) busts the cache. Returns {} if the backtest hasn't been run yet."""
    try:
        tr = pd.read_csv(BT_TRADES_FILE)
    except Exception:
        return {}
    if tr.empty or "symbol" not in tr.columns or "pnl" not in tr.columns:
        return {}
    out = {}
    for sym, g in tr.groupby("symbol"):
        w = int((g["pnl"] > 0).sum()); l = int((g["pnl"] <= 0).sum())
        if w + l:
            out[str(sym)] = (w, l)
    return out

# ---- persistent LIVE track record (forward-test log; accumulates day by day) ----
SIGNAL_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signal_log.json")

def load_log():
    if os.path.exists(SIGNAL_LOG_FILE):
        try:
            return json.load(open(SIGNAL_LOG_FILE, encoding="utf-8"))
        except Exception:
            return []
    return []

def save_log(log):
    try:
        json.dump(log, open(SIGNAL_LOG_FILE, "w", encoding="utf-8"), indent=1, default=str)
    except Exception:
        pass

def save_daily_csv(log, date):
    """Archive that day's signals to daily_YYYYMMDD.csv (updated live; becomes the EOD snapshot)."""
    todays = [e for e in log if e.get("date") == date]
    if not todays:
        return
    try:
        fn = os.path.join(os.path.dirname(SIGNAL_LOG_FILE), f"daily_{date.replace('-', '')}.csv")
        pd.DataFrame(todays).to_csv(fn, index=False)
    except Exception:
        pass

# ------------------------------------------------------------------ styling
st.markdown("""
<style>
:root{--bg:#0b0e14;--panel:#141926;--panel2:#1b2233;--line:#242c40;
 --green:#00d69e;--red:#ff5470;--amber:#ffb547;--blue:#4aa8ff;--txt:#e8ecf5;--mut:#8b95ad;}
.stApp{background:radial-gradient(1200px 600px at 15% -10%,#16203a 0%,var(--bg) 55%);}
#MainMenu,footer,header{visibility:hidden;}
.block-container{padding-top:1.2rem;max-width:1500px;}
.hero{background:linear-gradient(100deg,#101833,#1a2547 60%,#0f1a30);border:1px solid var(--line);
 border-radius:18px;padding:18px 26px;margin-bottom:14px;box-shadow:0 10px 40px rgba(0,0,0,.35);}
.hero h1{font-size:30px;margin:0;letter-spacing:.5px;color:var(--txt);
 background:linear-gradient(90deg,#00d69e,#4aa8ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.hero p{color:var(--mut);margin:2px 0 0;font-size:13px;}
.kpi{background:linear-gradient(160deg,var(--panel2),var(--panel));border:1px solid var(--line);
 border-radius:14px;padding:14px 16px;text-align:center;}
.kpi .v{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;}
.kpi .l{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:1px;margin-top:2px;}
.card{background:linear-gradient(165deg,var(--panel2),var(--panel));border:1px solid var(--line);
 border-radius:16px;padding:16px 18px;margin-bottom:14px;position:relative;overflow:hidden;
 box-shadow:0 8px 26px rgba(0,0,0,.30);transition:transform .12s ease;}
.card:hover{transform:translateY(-2px);border-color:#33507a;}
.card.CE{border-left:4px solid var(--green);} .card.PE{border-left:4px solid var(--red);}
.badge{display:inline-block;font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px;letter-spacing:.6px;}
.b-CE{background:rgba(0,214,158,.15);color:var(--green);} .b-PE{background:rgba(255,84,112,.15);color:var(--red);}
.b-ACTIVE{background:rgba(0,214,158,.18);color:var(--green);border:1px solid rgba(0,214,158,.4);}
.b-NEAR{background:rgba(255,181,71,.16);color:var(--amber);border:1px solid rgba(255,181,71,.35);}
.b-WATCH{background:rgba(74,168,255,.14);color:var(--blue);border:1px solid rgba(74,168,255,.3);}
.sym{font-size:20px;font-weight:800;color:var(--txt);}
.px{font-variant-numeric:tabular-nums;font-weight:700;}
.lvl{display:flex;justify-content:space-between;font-size:12px;padding:3px 0;border-bottom:1px dashed #222c42;}
.lvl b{font-variant-numeric:tabular-nums;color:var(--txt);}
.mut{color:var(--mut);} .g{color:var(--green);} .r{color:var(--red);} .a{color:var(--amber);} .bl{color:var(--blue);}
.bar{height:6px;background:#222c42;border-radius:6px;overflow:hidden;margin-top:8px;}
.bar>i{display:block;height:100%;background:linear-gradient(90deg,#00d69e,#4aa8ff);}
.chip{display:inline-block;font-size:10px;background:#202940;color:var(--mut);border:1px solid var(--line);
 border-radius:6px;padding:2px 7px;margin:2px 3px 0 0;}
.opt{font-size:11px;color:var(--mut);margin-top:8px;background:#10192c;border:1px solid var(--line);
 border-radius:8px;padding:7px 9px;}
.live-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#00d69e;margin-right:5px;
 animation:pulse 1.4s infinite;}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(0,214,158,.6)}70%{box-shadow:0 0 0 8px rgba(0,214,158,0)}
 100%{box-shadow:0 0 0 0 rgba(0,214,158,0)}}
.feedpill{font-size:12px;font-weight:700;padding:4px 12px;border-radius:20px;border:1px solid var(--line);}
.circ{width:54px;height:54px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none;}
.circ-in{width:42px;height:42px;border-radius:50%;background:#141926;display:flex;flex-direction:column;
 align-items:center;justify-content:center;line-height:1;}
.circ-in b{font-size:13px;font-weight:800;color:#e8ecf5;} .circ-in span{font-size:7px;color:#8b95ad;letter-spacing:.3px;}
.lockwrap{position:relative;}
.locked{filter:blur(5px);pointer-events:none;user-select:none;opacity:.65;}
.lockbadge{position:absolute;top:42%;left:0;right:0;text-align:center;z-index:3;font-size:12px;
 font-weight:800;color:#ffb547;text-shadow:0 1px 6px #000;}
/* ---- stock-market loading animation ---- */
.ml-wrap{display:flex;flex-direction:column;align-items:center;gap:13px;padding:30px 0;}
.ml-bars{display:flex;align-items:flex-end;gap:5px;height:58px;}
.ml-bar{width:9px;border-radius:2px;animation:mlpulse 1s ease-in-out infinite;}
.ml-bar.g{background:linear-gradient(180deg,#00d69e,#0a7f61);box-shadow:0 0 8px rgba(0,214,158,.4);}
.ml-bar.r{background:linear-gradient(180deg,#ff5470,#a1263c);box-shadow:0 0 8px rgba(255,84,112,.4);}
@keyframes mlpulse{0%,100%{height:14px;opacity:.45;}50%{height:54px;opacity:1;}}
.ml-msg{color:#c7cede;font-size:13px;letter-spacing:.4px;font-weight:600;}
.ml-track{width:min(440px,72vw);height:6px;background:#1b2233;border-radius:6px;overflow:hidden;position:relative;}
.ml-fill{position:absolute;top:0;height:100%;width:38%;border-radius:6px;
 background:linear-gradient(90deg,rgba(0,214,158,0),#00d69e,#4aa8ff,rgba(74,168,255,0));
 animation:mlslide 1.25s ease-in-out infinite;}
@keyframes mlslide{0%{left:-38%;}100%{left:100%;}}
.ml-fill2{height:100%;border-radius:6px;transition:width .25s ease;
 background:linear-gradient(90deg,#00d69e,#4aa8ff);box-shadow:0 0 10px rgba(0,214,158,.5);}
.ml-pct{color:#e8ecf5;font-weight:800;font-variant-numeric:tabular-nums;}
</style>
""", unsafe_allow_html=True)

_LOADER_BARS = "".join(
    f'<span class="ml-bar {c}" style="animation-delay:{i*0.08:.2f}s"></span>'
    for i, c in enumerate(["g","r","g","g","r","g","r","g","g","r","g","g"]))

def market_loader(msg="Loading market data…"):
    """Indeterminate stock-market loader (pulsing candlesticks + sweeping ticker bar)."""
    return (f'<div class="ml-wrap"><div class="ml-bars">{_LOADER_BARS}</div>'
            f'<div class="ml-msg">📈 {msg}</div>'
            f'<div class="ml-track"><div class="ml-fill"></div></div></div>')

def market_loader_pct(msg, pct):
    """Determinate loader with a real % bar so you can see how much is done."""
    pct = max(0.0, min(100.0, pct))
    return (f'<div class="ml-wrap"><div class="ml-bars">{_LOADER_BARS}</div>'
            f'<div class="ml-msg">📈 {msg} &nbsp;<span class="ml-pct">{pct:.0f}%</span></div>'
            f'<div class="ml-track"><div class="ml-fill2" style="width:{pct:.1f}%"></div></div></div>')

def sparkline(vals, up):
    if not vals or len(vals) < 2: return ""
    lo,hi = min(vals),max(vals); rng = (hi-lo) or 1
    w,h = 150,34; step = w/(len(vals)-1)
    pts = " ".join(f"{i*step:.1f},{h-(v-lo)/rng*h:.1f}" for i,v in enumerate(vals))
    col = "#00d69e" if up else "#ff5470"
    return (f'<svg width="{w}" height="{h}">'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.8"/></svg>')

def score_circle(pct, trials=None):
    """Conic-gradient success ring (backtested win-rate). trials=(wins,losses) -> hover tooltip."""
    if trials:
        w, l = trials; tip = f'title="{w}/{w+l} winning trades in backtest"'
    else:
        tip = 'title="No backtested trades yet"'
    if pct is None:
        return (f'<div class="circ" {tip} style="background:conic-gradient(#2a3350 0 100%);cursor:help;">'
                '<div class="circ-in"><b class="mut">NA</b><span>SUCCESS</span></div></div>')
    col = "#00d69e" if pct >= 55 else ("#ffb547" if pct >= 45 else "#ff5470")
    return (f'<div class="circ" {tip} style="cursor:help;'
            f'background:conic-gradient({col} {pct*3.6:.0f}deg,#222c42 0);">'
            f'<div class="circ-in"><b>{pct:.0f}%</b><span>SUCCESS</span></div></div>')

def card_html(r):
    up = r["chg"] >= 0
    frac = int(r["met"]/r["total"]*100)
    chips = "".join(f'<span class="chip">✗ {m}</span>' for m in r["missing"][:4])
    play = ("SELL Bull-Put" if r["side"]=="CE" else "SELL Bear-Call")
    live = r.get("live"); pnl = r.get("live_pnl", 0.0)
    dot = '<span class="live-dot"></span>' if live else ''
    prox = (f'<span class="{r.get("prox_cls","mut")}" style="font-size:12px;font-weight:700;">'
            f'{r.get("prox_tag","")}</span>') if live else ''
    pnl_line = (f'<div class="mut" style="font-size:11px;margin-top:2px;">'
                f'{dot}LIVE ₹{r["ltp"]:.1f} · vs entry '
                f'<b class="{"g" if pnl>=0 else "r"}">{pnl:+.2f}%</b> &nbsp; {prox}</div>') if live else ''
    levels = f"""
      <div class="lvl" style="margin-top:6px;"><span class="mut">Entry (fixed)</span><b>₹{r['entry']:.1f}</b></div>
      <div class="lvl"><span class="mut">Stop-Loss</span><b class="r">₹{r['sl']:.1f}</b></div>
      <div class="lvl"><span class="mut">Target 1 · 2 · 3</span>
        <b class="g">₹{r['t1']:.1f} · ₹{r['t2']:.1f} · ₹{r['t3']:.1f}</b></div>
      <div class="lvl"><span class="mut">ADX / RSI / R:R</span>
        <b>{r['adx']:.0f} · {r['rsi']:.0f} · <span class="bl">{r['rr']}</span></b></div>
      <div class="bar"><i style="width:{frac}%"></i></div>
      <div class="mut" style="font-size:11px;margin-top:4px;">Conditions met {r['met']}/{r['total']}</div>
      <div class="opt">💡 <b>Option play:</b> {play} spread &nbsp;|&nbsp; short ₹{r['short_k']:.0f} · long ₹{r['long_k']:.0f}
        &nbsp;|&nbsp; est. credit ₹{r['credit']:.1f} &nbsp;|&nbsp; TP 60% · SL 1.5× · IV {r['iv']*100:.0f}%</div>
      {'<div style="margin-top:6px;">'+chips+'</div>' if chips else ''}"""
    body = levels                     # always show entry/SL/targets (no market-hours lock)
    return f"""
    <div class="card {r['side']}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div><span class="sym">{r['symbol']}</span>
          <span class="badge b-{r['side']}">{'CALL / '+play.split()[1] if r['side']=='CE' else 'PUT / '+play.split()[1]}</span>
          <div style="margin-top:5px;"><span class="badge b-{r['status']}">{r['status']}</span></div></div>
        {score_circle(r.get('score'), r.get('trials'))}
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin:6px 0 2px;">
        <div class="px" style="font-size:22px;">₹{r['price']:.1f}
          <span class="mut" style="font-size:10px;">LTP</span>
          <span class="{'g' if up else 'r'}" style="font-size:13px;">{r['chg']:+.2f}%</span></div>
        <div>{sparkline(r['spark'], up)}</div>
      </div>
      {pnl_line}
      {body}
    </div>"""

# ------------------------------------------------------------------ sidebar
st.sidebar.markdown("### ⚙️  Scanner Controls")
uni_choice = st.sidebar.radio("Universe", ["⚡ Quick (top 40)", "🌐 Full (all 214)"],
    index=0, key="uni_choice",
    help="Quick loads instantly on login. Full re-scans ~214 stocks — slower, use when you need everything.")
quick = uni_choice.startswith("⚡")
universe = DEFAULT_UNIVERSE if quick else full_universe()
picked = [s for s in universe if is_large_cap(s)]     # market cap >= Rs 40,000 cr gate
st.sidebar.caption(f"Scanning **{len(picked)}** large-cap stocks (≥ ₹40,000 cr)"
                   + ("  (fast)" if quick else "  (full — allow time to load)"))
side_filter = st.sidebar.radio("Side", ["Both","CE only","PE only"], horizontal=True)
MIN_SUCCESS = st.sidebar.slider("Min backtest success %", 0, 100, 0, 5,
    help="Only show setups whose backtested win-rate is at least this. 0 = show all (incl. NA).")
st.sidebar.markdown("#### Strategy thresholds")
P = {
    "ADX": st.sidebar.slider("15m ADX min", 15, 40, 25),
    "RL":  st.sidebar.slider("15m RSI long >", 50, 70, 55),
    "RS":  st.sidebar.slider("15m RSI short <", 30, 50, 45),
    "VOL": st.sidebar.slider("Volume surge x", 1.0, 2.0, 1.2, 0.1),
}
# saved (DPAPI-encrypted) Angel creds, if present on this machine — import guarded for cloud/Linux
try:
    from secure_creds import load_angel_creds
    ANGEL_SAVED = load_angel_creds()
except Exception:
    ANGEL_SAVED = None

st.sidebar.markdown("#### 📡 Live feed — Angel One")
FEED_MODE = "angel"                                 # Angel only (Yahoo feed removed)
KITE_LIVE = True
creds = None
if ANGEL_SAVED:
    creds = ANGEL_SAVED
    st.sidebar.success("🔴 **Angel One live** · using your saved encrypted credentials (auto-login).")
else:
    try:    sec = dict(st.secrets.get("angel", {}))
    except Exception: sec = {}
    ak  = st.sidebar.text_input("Angel API key", value=sec.get("api_key",""), type="password")
    cc  = st.sidebar.text_input("Client code (login ID)", value=sec.get("client_code",""))
    pin = st.sidebar.text_input("PIN / password", value=sec.get("pin",""), type="password")
    tot = st.sidebar.text_input("TOTP secret (base32)", value=sec.get("totp_secret",""), type="password")
    creds = {"api_key": ak, "client_code": cc, "pin": pin, "totp_secret": tot}
    st.sidebar.caption("Free from smartapi.angelbroking.com. Or run `secure_creds.py encrypt` to save "
                       "them DPAPI-encrypted for auto-login.")

st.sidebar.markdown("#### 🔄 Refresh")
cbtn = st.sidebar.columns(2)
if cbtn[0].button("🟢 Quotes", width="stretch", help="Pull the latest ticks now"):
    st.rerun()
if cbtn[1].button("♻ Reload bars", width="stretch", help="Re-download historical bars"):
    st.cache_data.clear()
    compute_cache().clear()          # drop cached scan / backtest / load flags
    st.rerun()

# Angel streams real-time ticks -> in-place sync (no page reload). NIFTY header ticks every 2s
# via its own fragment; the full board syncs every 10s to stay light on the big universe.
st.sidebar.caption("🔴 **Live via Angel** — real-time ticks; prices, targets and the track record "
                   "update automatically. No manual reload needed.")
if _HAS_AUTOREFRESH:
    st_autorefresh(interval=10000, key="auto_refresh")

# ------------------------------------------------------------------ fetch + scan
CACHE = compute_cache()                      # process-global; persists across reloads/sessions
picked = picked or DEFAULT_UNIVERSE
symbols = sorted(set(picked) | {"NIFTY"})
# fetch in chunks; show the % loader ONLY on a genuinely fresh load
CHUNK = 40
chunks = [symbols[i:i+CHUNK] for i in range(0, len(symbols), CHUNK)]
steps_total = max(1, len(chunks) * 2)
_fresh_load = CACHE.get("loaded_syms") != tuple(symbols)
_load = st.empty() if _fresh_load else None
if _fresh_load:
    _load.markdown(market_loader_pct(f"Fetching market data · {len(symbols)} stocks", 0), unsafe_allow_html=True)
daily, intr, done = {}, {}, 0
for ch in chunks:
    daily.update(get_daily(tuple(ch)))
    done += 1
    if _fresh_load: _load.markdown(market_loader_pct("Loading daily bars", done/steps_total*100), unsafe_allow_html=True)
for ch in chunks:
    intr.update(get_15m(tuple(ch)))
    done += 1
    if _fresh_load: _load.markdown(market_loader_pct("Loading 15m bars", done/steps_total*100), unsafe_allow_html=True)
if _fresh_load: _load.empty()
CACHE["loaded_syms"] = tuple(symbols)

nifty_d = daily.get("NIFTY"); nifty15 = intr.get("NIFTY")
regime_bull = bool(nifty_d["Close"].iloc[-1] > ema(nifty_d["Close"],50).iloc[-1]) if nifty_d is not None else True
nifty15_close = nifty15["Close"] if nifty15 is not None else None
regime_series = (pd.Series(np.where(nifty_d["Close"] > ema(nifty_d["Close"],50), 1, -1), index=nifty_d.index)
                 if nifty_d is not None else None)

# ---- start / update the real-time feed (seed with last Yahoo price + prev-day close) ----
seed = {}
for s in symbols:
    if s in intr and len(intr[s]):
        last = float(intr[s]["Close"].iloc[-1])
        prev = float(daily[s]["Close"].iloc[-2]) if s in daily and len(daily[s]) > 1 else last
        seed[s] = (last, prev)
feed = get_feed()
feed.configure(FEED_MODE, symbols, seed, creds)

def live_15m(s):
    """Yahoo warm-up bars + new 15m candles built live from Angel ticks (no re-fetch needed)."""
    base = intr.get(s)
    nb = feed.get_new_candles(s)
    if nb is None or len(nb) == 0:
        return base
    if base is None or len(base) == 0:
        return nb
    tail = nb[nb.index > base.index[-1]]                 # only candles newer than the warm-up
    return pd.concat([base, tail]) if len(tail) else base

nlive_bars = live_15m("NIFTY")
nifty15_close = nlive_bars["Close"] if nlive_bars is not None and len(nlive_bars) else nifty15_close

# ---- SIGNAL SCAN — heavy INDICATORS recompute at most ONCE per 15-min candle. The cache key is
#      TIME-based (not the fetched-symbol count), so partial / rate-limited Yahoo fetches can NEVER
#      trigger an endless recompute loop. New candles come from Angel ticks (no 214-symbol re-fetch).
feat_bucket = now_ist().strftime("%Y%m%d-%H-") + str(now_ist().minute // 15)
picked_set = set(picked)
if CACHE.get("feat_bucket") != feat_bucket:
    syms_f = [s for s in picked if s not in ("NIFTY",) and s in daily and s in intr]
    _fl = st.empty() if len(syms_f) > 60 else None
    feats = dict(CACHE.get("feats", {}))               # keep prior, refresh what we have now
    for i, s in enumerate(syms_f):
        try:
            mc = closed_15m(live_15m(s))               # Yahoo warm-up + live Angel candles, closed only
            if mc is None or len(mc) < 210: continue
            feats[s] = symbol_features(s, daily[s], mc, nifty15_close, regime_bull)
        except Exception:
            continue
        if _fl and i % 20 == 0:
            _fl.markdown(market_loader_pct("Computing indicators", (i+1)/max(1, len(syms_f))*100),
                         unsafe_allow_html=True)
    if _fl: _fl.empty()
    CACHE["feat_bucket"] = feat_bucket
    CACHE["feats"] = feats
rows = []
for s, f in CACHE.get("feats", {}).items():
    if s not in picked_set: continue                   # only the current universe
    try:
        r = row_from_features(s, f, P)                 # cheap threshold step (re-runs on slider change)
        if r: rows.append(r)
    except Exception:
        continue
for r in rows:
    live_overlay(r, feed)                              # cheap: overlay live LTP/proximity
if side_filter != "Both":
    rows = [r for r in rows if r["side"] == side_filter[:2]]

# ==== LIVE forward-test track record (no historical backtest) =========================
# Every ACTIVE signal is recorded the moment it fires and tracked in real time against the
# live price. Wins/losses accumulate in signal_log.json day by day. Success scores are the
# LIVE win-rate of each stock's recorded signals — building up from today onward.
LOG = load_log()
today = now_ist().strftime("%Y-%m-%d")
_expired = set()
for e in LOG:                                        # expire prior days' still-open signals at EOD
    if e.get("status") == "OPEN" and e.get("date") != today:
        e["status"] = "EOD"; e["outcome"] = "EOD (unresolved)"; _expired.add(e["date"])
if _expired:                                         # finalise those dates' snapshots
    save_log(LOG)
    for _dt in _expired:
        save_daily_csv(LOG, _dt)
by_id = {e["id"]: e for e in LOG}
MARKET_OPEN = market_open()

# ==== HISTORICAL BACKTEST success — per-symbol win-rate from the standalone daily backtest ==
# Read from trades.csv (produced by `python backtest_strategy.py`, 2018->today over ALL large-
# caps). This works even if the dashboard was never opened before — the backtest is run
# separately and the ring just reflects it. Re-run the backtest to refresh these numbers.
BT_SCORES = load_backtest_scores(os.path.getmtime(BT_TRADES_FILE) if os.path.exists(BT_TRADES_FILE) else 0)
# Success ring + MIN_SUCCESS filter come from the historical backtest, not the live log.
SYM_SCORE  = {s: round(w / (w + l) * 100, 1) for s, (w, l) in BT_SCORES.items() if (w + l)}
SYM_TRIALS = {s: (w, l) for s, (w, l) in BT_SCORES.items()}
for r in rows:
    r["score"] = SYM_SCORE.get(r["symbol"]); r["trials"] = SYM_TRIALS.get(r["symbol"])
if MIN_SUCCESS > 0:
    rows = [r for r in rows if (r.get("score") or 0) >= MIN_SUCCESS]

# record new ACTIVE signals + track OPEN ones against the live tick
resolved_today = {e["symbol"] for e in LOG if e.get("date") == today and e.get("status") in ("WIN", "LOSS", "EOD")}
changed = False
active_open = []
for r in [x for x in rows if x["status"] == "ACTIVE"]:
    sym = r["symbol"]; sid = f"{sym}-{today}"
    e = by_id.get(sid)
    if e and e["status"] != "OPEN":
        continue                                     # already resolved today -> not active any more
    if e is None:                                    # NEW signal today -> record it (freezes entry/levels)
        e = {"id": sid, "date": today, "symbol": sym, "side": r["side"],
             "entry_ts": now_ist().strftime("%H:%M"), "entry": round(r["entry"], 2),
             "sl": round(r["sl"], 2), "t1": round(r["t1"], 2), "t2": round(r["t2"], 2),
             "t3": round(r["t3"], 2), "status": "OPEN", "exit_ts": None, "exit": None, "outcome": None}
        LOG.append(e); by_id[sid] = e; changed = True
    r["entry"], r["sl"], r["t1"], r["t2"], r["t3"] = e["entry"], e["sl"], e["t1"], e["t2"], e["t3"]
    live_overlay(r, feed)                            # proximity/P&L vs the frozen entry
    ltp = r["ltp"]; lng = r["side"] == "CE"
    denom = (e["t1"] - e["entry"]) if lng else (e["entry"] - e["t1"])
    r["t1_progress"] = ((ltp - e["entry"]) if lng else (e["entry"] - ltp)) / denom if denom else 0.0
    hit_t1 = (ltp >= e["t1"]) if lng else (ltp <= e["t1"])
    hit_sl = (ltp <= e["sl"]) if lng else (ltp >= e["sl"])
    if hit_t1 or hit_sl:
        e["status"] = "WIN" if hit_t1 else "LOSS"
        e["outcome"] = "TARGET ✅" if hit_t1 else "STOP ❌"
        e["exit"] = round(ltp, 2); e["exit_ts"] = now_ist().strftime("%H:%M:%S")
        changed = True
        continue
    active_open.append(r)
if changed:
    save_log(LOG)
    save_daily_csv(LOG, today)              # keep today's dated CSV snapshot current (EOD archive)

active = sorted(active_open, key=lambda x: (-(x["score"] or 0), -x["adx"]))
near   = sorted([r for r in rows if r["status"]=="NEAR"  and r["symbol"] not in resolved_today],
                key=lambda x: (-(x["score"] or 0), -x["met"]))
watch  = sorted([r for r in rows if r["status"]=="WATCH" and r["symbol"] not in resolved_today],
                key=lambda x: (-(x["score"] or 0), -x["met"]))
closed_list = sorted([e for e in LOG if e.get("date")==today and e.get("status") in ("WIN","LOSS")],
                     key=lambda x: x.get("exit_ts",""), reverse=True)
PINNED = st.session_state.setdefault("pinned", [])

# ------------------------------------------------------------------ reusable renderers
now = datetime.now().strftime("%d %b %Y  %H:%M:%S")
# smaller periods = faster download while still plenty of candles
TF_MAP = {"5m":("5m","30d"), "15m":("15m","60d"), "1h":("60m","120d"),
          "1D":("1d","1y"), "1W":("1wk","3y"), "1M":("1mo","8y")}

def radar_fig(d, kp):
    """Radial (polar) snapshot of O/H/L/C/EMA/RSI/ADX, price metrics normalised to 0–100."""
    last = d.iloc[-1]
    lo = float(d["Low"].min()); hi = float(d["High"].max()); rng = (hi - lo) or 1.0
    def npx(x): return max(0.0, min(100.0, (float(x)-lo)/rng*100))
    cats = ["Open","High","Low","Close","EMA5","EMA43","EMA200","RSI","ADX"]
    vals = [npx(last["Open"]), npx(last["High"]), npx(last["Low"]), npx(last["Close"]),
            npx(last["e5"]), npx(last["e43"]), npx(last["e200"]),
            max(0,min(100,float(last["rsi"]))), max(0,min(100,float(last["adx"])))]
    fig = go.Figure(go.Scatterpolar(r=vals+[vals[0]], theta=cats+[cats[0]], fill="toself",
        line=dict(color="#4aa8ff", width=2), fillcolor="rgba(74,168,255,.22)",
        hovertemplate="%{theta}: %{r:.0f}/100<extra></extra>"))
    fig.update_layout(template="plotly_dark", height=330, showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=34,r=34,t=24,b=24),
        polar=dict(bgcolor="#0d1220",
            radialaxis=dict(range=[0,100], gridcolor="rgba(120,140,180,.15)", tickfont=dict(size=8)),
            angularaxis=dict(gridcolor="rgba(120,140,180,.18)", tickfont=dict(size=10))))
    return fig

def render_chart(sym, r, kp, preloaded15=None):
    """TradingView-style chart with timeframe buttons, scroll-zoom and zoom persistence."""
    c1, c2, c3 = st.columns([3, 2, 1.3])
    tf_label = c1.radio("Timeframe", list(TF_MAP), index=1, horizontal=True, key=f"{kp}_tf")
    show_n = c2.select_slider("Candles in view", options=[50,80,120,180,250,400],
                              value=120, key=f"{kp}_n")
    show_radar = c3.toggle("🧭 Radar", value=True, key=f"{kp}_radar_on",
                           help="Show/hide the radial indicator snapshot below the chart.")
    # ---- late-entry alert (item 4): price already ran toward Target 1 ----
    if r and r.get("t1_progress") is not None:
        prog = r["t1_progress"]
        if prog >= 1.0:
            st.error(f"🏁 {sym}: price has **already reached Target 1** — entry window closed.")
        elif prog >= 0.5:
            st.warning(f"⚠ LATE ENTRY on {sym}: price already moved **{prog*100:.0f}% toward Target 1** "
                       f"from the entry (₹{r['entry']:.1f} → now ₹{r['ltp']:.1f}). Risk:reward is degraded.")
    iv_, per_ = TF_MAP[tf_label]
    if tf_label == "15m" and preloaded15 is not None and len(preloaded15) > 20:
        d = preloaded15                                  # reuse already-loaded data -> instant
    else:
        _cl = st.empty()
        _cl.markdown(market_loader(f"Loading {sym} · {tf_label} chart…"), unsafe_allow_html=True)
        d = get_ohlc(sym, iv_, per_)
        _cl.empty()
    if d is None or len(d) < 20:
        st.info("No data for this timeframe."); return
    d = d.tail(400).copy()
    d["e5"], d["e43"], d["e200"] = ema(d["Close"],5), ema(d["Close"],43), ema(d["Close"],200)
    mid = d["Close"].rolling(20).mean(); sdv = d["Close"].rolling(20).std()
    d["bbu"], d["bbl"] = mid + 2*sdv, mid - 2*sdv
    d["rsi"], d["adx"] = rsi(d["Close"]), adx(d)
    volc = np.where(d["Close"] >= d["Open"], "rgba(0,214,158,.45)", "rgba(255,84,112,.45)")
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.66,0.17,0.17],
                        vertical_spacing=0.02)
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"],
        close=d["Close"], name="Price", increasing_line_color="#00d69e",
        decreasing_line_color="#ff5470", increasing_fillcolor="#00d69e",
        decreasing_fillcolor="#ff5470"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=d["bbu"], line=dict(width=1, color="rgba(120,140,180,.4)"),
        name="BB", showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=d["bbl"], line=dict(width=1, color="rgba(120,140,180,.4)"),
        fill="tonexty", fillcolor="rgba(80,110,170,.05)", name="BB", showlegend=False), row=1, col=1)
    for cc_, colr in [("e5","#4aa8ff"), ("e43","#ffb547"), ("e200","#b088ff")]:
        fig.add_trace(go.Scatter(x=d.index, y=d[cc_], line=dict(width=1.2, color=colr),
            name=cc_.upper()), row=1, col=1)
    if r and tf_label in ("5m", "15m", "1h"):
        for y,txt,colr in [(r["entry"],"Entry","#e8ecf5"), (r["sl"],"SL","#ff5470"),
                           (r["t1"],"T1","#00d69e"), (r["t2"],"T2","#00d69e"), (r["t3"],"T3","#00d69e")]:
            fig.add_hline(y=y, line=dict(color=colr, width=1, dash="dot"),
                          annotation_text=txt, annotation_position="right", row=1, col=1)
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], marker_color=volc, name="Vol",
        showlegend=False), row=2, col=1)
    fig.add_trace(go.Scatter(x=d.index, y=d["rsi"], line=dict(color="#b088ff", width=1.2),
        name="RSI", showlegend=False), row=3, col=1)
    fig.add_hline(y=70, line=dict(color="#8b95ad", width=.6, dash="dash"), row=3, col=1)
    fig.add_hline(y=30, line=dict(color="#8b95ad", width=.6, dash="dash"), row=3, col=1)
    brk = [dict(bounds=["sat","mon"])]
    if tf_label in ("5m","15m","1h"):
        brk.append(dict(bounds=[15.5, 9.25], pattern="hour"))
    x0 = d.index[max(0, len(d)-show_n)]
    fig.update_xaxes(rangebreaks=brk, range=[x0, d.index[-1]], gridcolor="rgba(120,140,180,.06)",
                     rangeslider_visible=False, fixedrange=False)
    fig.update_yaxes(side="right", gridcolor="rgba(120,140,180,.07)", fixedrange=False)
    fig.update_layout(template="plotly_dark", height=760, dragmode="pan", hovermode="x unified",
        uirevision=f"{sym}|{tf_label}|{show_n}",           # keeps zoom across auto-reruns
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0d1220", margin=dict(l=6, r=58, t=10, b=6),
        legend=dict(orientation="h", y=1.02, x=0, font=dict(size=11)))
    st.plotly_chart(fig, width="stretch", key=f"{kp}_fig", config={
        "scrollZoom": True, "displaylogo": False, "doubleClick": "reset",
        "displayModeBar": True, "modeBarButtonsToRemove": ["select2d","lasso2d"], "responsive": True})
    st.caption("🖱️ **Scroll** = zoom · **drag** = pan · **double-click** = reset · "
               "use the toolbar (top-right of chart) or 'Candles in view' to adjust.")
    last = d["Close"].iloc[-1]; chg = (last/d["Close"].iloc[-2]-1)*100
    mm = st.columns(4)
    mm[0].metric(f"{sym} · {tf_label}", f"₹{last:,.1f}", f"{chg:+.2f}%")
    mm[1].metric("RSI(14)", f"{d['rsi'].iloc[-1]:.0f}")
    mm[2].metric("ADX(14)", f"{d['adx'].iloc[-1]:.0f}")
    mm[3].metric("Live signal", f"{r['side']} · {r['status']}" if r else "—")
    # ---- radial indicator snapshot (O/H/L/C/EMA/RSI/ADX) — toggleable ----
    if show_radar:
        lo = d.iloc[-1]
        rc = st.columns([1, 1])
        with rc[0]:
            st.markdown("###### 🧭 Indicator radar  ·  price metrics scaled 0–100 to the visible range")
            st.plotly_chart(radar_fig(d, kp), width="stretch", key=f"{kp}_radar",
                            config={"displayModeBar": False})
        with rc[1]:
            st.markdown("###### Live values")
            vt = pd.DataFrame({"Metric": ["Open","High","Low","Close","EMA5","EMA43","EMA200","RSI(14)","ADX(14)"],
                               "Value": [f"₹{lo['Open']:.1f}", f"₹{lo['High']:.1f}", f"₹{lo['Low']:.1f}",
                                         f"₹{lo['Close']:.1f}", f"₹{lo['e5']:.1f}", f"₹{lo['e43']:.1f}",
                                         f"₹{lo['e200']:.1f}", f"{lo['rsi']:.1f}", f"{lo['adx']:.1f}"]})
            st.dataframe(vt, width="stretch", hide_index=True, height=350)

def setup_tab(container, items, fkey, kp, empty_msg, sub=None, allow_pin=False):
    with container:
        if allow_pin:
            pc = st.columns([3, 1])
            addp = pc[0].text_input("➕ Pin a symbol to priority (a trade you've entered)",
                                    key=f"{kp}_pinadd", placeholder="e.g. RELIANCE",
                                    label_visibility="collapsed").strip().upper()
            if pc[1].button("➕ Pin", key=f"{kp}_pinbtn", width="stretch"):
                if addp and addp not in PINNED:
                    PINNED.append(addp); st.rerun()
        fsym = st.session_state.get(fkey)
        if fsym and any(x["symbol"] == fsym for x in items):
            hc = st.columns([6, 1])
            hc[0].markdown(f"#### 📊 {fsym}")
            if hc[1].button("✖ Close", key=f"{kp}_close"):
                st.session_state[fkey] = None; st.rerun()
            render_chart(fsym, next(x for x in items if x["symbol"] == fsym), kp,
                         preloaded15=intr.get(fsym))
            st.divider()
        if not items:
            st.info(empty_msg); return
        if sub: st.caption(sub)
        # pinned rows float to the top (priority)
        ordered = [r for r in items if r["symbol"] in PINNED] + \
                  [r for r in items if r["symbol"] not in PINNED]
        cols = st.columns(3)
        for i, r in enumerate(ordered):
            with cols[i % 3]:
                if r["symbol"] in PINNED:
                    st.markdown("<div style='color:#ffb547;font-size:11px;font-weight:700;'>📌 PRIORITY</div>",
                                unsafe_allow_html=True)
                st.markdown(card_html(r), unsafe_allow_html=True)
                b = st.columns([3, 1])
                if b[0].button(f"📈 {r['symbol']} chart", key=f"{kp}_{r['symbol']}", width="stretch"):
                    st.session_state[fkey] = r["symbol"]; st.rerun()
                is_pin = r["symbol"] in PINNED
                if b[1].button("📍" if is_pin else "📌", key=f"{kp}pin_{r['symbol']}",
                               help="Unpin" if is_pin else "Pin to priority"):
                    (PINNED.remove(r["symbol"]) if is_pin else PINNED.append(r["symbol"])); st.rerun()

def render_settings():
    st.markdown("##### 👥 User management")
    st.caption(f"Signed in as **{CURRENT_USER}** · superadmin")
    users = load_users()
    for name in list(users):
        cols = st.columns([2, 3, 1])
        cols[0].write(f"**{name}**" + (" ⭐" if users[name].get("role") == "superadmin" else ""))
        cols[1].caption("ALL modules" if users[name].get("role") == "superadmin"
                        else (", ".join(user_modules(users, name)) or "no access"))
        if name != SUPERADMIN and cols[2].button("🗑", key=f"deluser_{name}"):
            del users[name]; save_users(users); st.rerun()
    st.divider()
    st.markdown("##### ➕ Add / update user")
    with st.form("adduser_form"):
        nu = st.text_input("Username")
        npw = st.text_input("Password", type="password")
        nmods = st.multiselect("Allowed modules", ALL_MODULES, default=ALL_MODULES)
        if st.form_submit_button("Save user"):
            if nu and npw:
                users[nu] = {"password": npw, "role": "user", "modules": nmods}
                save_users(users); st.success(f"Saved {nu}"); st.rerun()
            else:
                st.warning("Enter both a username and password.")
    st.caption("Note: on Streamlit Cloud the user list resets on redeploy (disk is temporary). "
               "For permanent multi-user storage, use a database.")

# ------------------------------------------------------------------ alerts engine
def current_metric(sym, metric):
    m = intr.get(sym)
    if m is None or len(m) < 210:
        return None
    c = m["Close"]
    if metric == "Price":
        lv = feed.get(sym); return float(lv["ltp"]) if lv else float(c.iloc[-1])
    if metric == "EMA13":  return float(ema(c,13).iloc[-1])
    if metric == "EMA50":  return float(ema(c,50).iloc[-1])
    if metric == "EMA200": return float(ema(c,200).iloc[-1])
    if metric == "ADX":    return float(adx(m).iloc[-1])
    if metric == "RSI":    return float(rsi(c).iloc[-1])
    return None

def check_alerts():
    log = st.session_state.setdefault("alert_log", [])
    fired_ids = st.session_state.setdefault("alert_fired", set())
    fresh = []
    def emit(key, msg):
        if key not in fired_ids:
            fired_ids.add(key); fresh.append(msg)
            log.insert(0, {"time": now_ist().strftime("%H:%M:%S"), "alert": msg})
    for a in st.session_state.get("alerts", []):
        val = current_metric(a["symbol"], a["metric"])
        if val is None: continue
        hit = (val > a["value"]) if a["op"] == ">" else (val < a["value"])
        k = a["id"]
        if hit: emit(k, f'{a["symbol"]} {a["metric"]} {a["op"]} {a["value"]}  (now {val:.1f})')
        elif k in fired_ids: fired_ids.discard(k)          # re-arm when it clears
    if st.session_state.get("notify_active", True):
        for r in active: emit(f'act_{r["symbol"]}', f'🎯 {r["symbol"]} is now an ACTIVE {r["side"]} setup')
    if st.session_state.get("notify_target", True):
        for r in active + near:
            if r.get("prox_tag","").startswith("🎯"):
                emit(f'tgt_{r["symbol"]}_{r["prox_tag"]}', f'✅ {r["symbol"]} {r["prox_tag"]}')
    if st.session_state.get("notify_sl", True):
        for r in active + near:
            if "SL hit" in r.get("prox_tag",""):
                emit(f'sl_{r["symbol"]}', f'⚠ {r["symbol"]} hit its stop-loss')
    st.session_state["alert_log"] = log[:60]
    return fresh

fresh_alerts = check_alerts()
for msg in fresh_alerts[:6]:
    st.toast(msg, icon="🔔")
if fresh_alerts:
    js = ";".join(f'new Notification("Harsh FnO Alert",{{body:{json.dumps(m)}}})' for m in fresh_alerts[:6])
    st_html("<script>if(window.Notification){if(Notification.permission==='granted'){"
            + js + "}else if(Notification.permission!=='denied'){Notification.requestPermission()"
            ".then(function(p){if(p==='granted'){" + js + "}});}}</script>", height=0)

# ------------------------------------------------------------------ top bar (settings · title · search)
_fs = feed.status
if _fs == "live":                  pill = ('<span class="feedpill" style="color:#00d69e;border-color:#00d69e;">'
                                 '<span class="live-dot"></span>LIVE · Angel One WebSocket</span>')
elif _fs.startswith("live-yahoo"): pill = ('<span class="feedpill" style="color:#00d69e;border-color:#2f6b52;">'
                                 '<span class="live-dot"></span>LIVE · Yahoo auto (~15m delay)</span>')
elif _fs == "live-demo": pill = ('<span class="feedpill" style="color:#ffb547;border-color:#6a5320;">'
                                 '<span class="live-dot" style="background:#ffb547;"></span>DEMO ticks (simulated)</span>')
elif _fs == "connecting":pill = '<span class="feedpill" style="color:#ffb547;">⏳ Connecting…</span>'
elif _fs == "off":       pill = '<span class="feedpill" style="color:#8b95ad;">⚪ Static (last close)</span>'
else:                    pill = f'<span class="feedpill" style="color:#ff5470;border-color:#5a2230;">🔴 {_fs}</span>'

tb = st.columns([1.0, 3.6, 1.6])
with tb[0]:
    if IS_ADMIN:
        with st.popover("⚙️ Settings"):
            render_settings()
    st.caption(f"👤 {CURRENT_USER}")
    if st.button("Logout", key="logout_btn"):
        for kk_ in ["authed","user","role","modules"]: st.session_state.pop(kk_, None)
        st.rerun()
with tb[2]:
    query = st.text_input("Search", placeholder="🔎  Search a stock…",
                          label_visibility="collapsed", key="topsearch").strip().upper()
with tb[1]:
    st.markdown(f"""<div class="hero" style="padding:12px 20px;margin:0;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
        <div><h1 style="font-size:23px;">📡 Harsh FnO API 1.0</h1>
          <p style="margin:1px 0 0;">{now} IST · scanning {len(picked)} large-cap stocks (≥ ₹40,000 cr)</p></div>
        <div>{pill}</div>
      </div></div>""", unsafe_allow_html=True)

# ---- search result (renders under the top bar when a query is typed) ----
if query:
    _sl = st.empty()
    _sl.markdown(market_loader(f"Searching {query}…"), unsafe_allow_html=True)
    d = daily.get(query)
    if d is None: d = get_ohlc(query, "1d", "2y")
    m = intr.get(query)
    if m is None: m = get_ohlc(query, "15m", "60d")
    _sl.empty()
    if d is None or m is None or len(d) < 210 or len(m) < 210:
        st.warning(f"Couldn't load enough data for **{query}** — check the symbol (NSE F&O names).")
    else:
        n15 = nifty15_close
        if n15 is None:
            nd = get_ohlc("NIFTY", "15m", "60d"); n15 = nd["Close"] if nd is not None else None
        sig = entry_signals(d, m, n15, regime_series, P)
        ls = last_signal(sig); trs = walk_trades(sig)
        cur = next((x for x in rows if x["symbol"] == query), None)
        with st.container(border=True):
            if ls is None:
                st.markdown(f"### {query}  ·  🔎 Last suggested entry: **NA**")
                st.caption("No entry signal was generated in the last 60 days for this stock.")
            else:
                ago = m.index[-1] - ls["ts"]
                match = next((t for t in trs if t["entry_ts"] == ls["ts"]), None)
                oc = (match or {}).get("outcome", "OPEN")
                octxt = {"WIN":"Hit target ✅","LOSS":"Hit stop ❌","OPEN":"Still open ⏳"}[oc]
                s1,s2,s3,s4 = st.columns(4)
                s1.metric(f"{query} — last entry", ls["ts"].strftime("%d %b, %H:%M"), f"{ls['side']}")
                s2.metric("How long ago", f"{ago.days}d {ago.seconds//3600}h")
                s3.metric("Entry price", f"₹{ls['price']:.1f}")
                s4.metric("Outcome", octxt)
            if cur:
                st.success(f"Right now **{query}** is a {cur['side']} · {cur['status']} setup "
                           f"({cur['met']}/{cur['total']} conditions).")

# ------------------------------------------------------------------ KPIs (NIFTY ticks live)
def _nifty_frag():
    lv = feed.get("NIFTY")
    if lv:
        px, chg = lv["ltp"], lv["chg"]
    elif nifty_d is not None:
        px = nifty_d["Close"].iloc[-1]; chg = (px/nifty_d["Close"].iloc[-2]-1)*100
    else:
        px, chg = float("nan"), 0.0
    cls = "g" if chg >= 0 else "r"
    dot = '<span class="live-dot" style="width:7px;height:7px;"></span>' if KITE_LIVE else ''
    st.markdown(f'<div class="kpi"><div class="v {cls}">{px:,.1f}'
                f'<span style="font-size:12px;"> {chg:+.2f}%</span></div>'
                f'<div class="l">{dot}NIFTY 50{" · LIVE" if KITE_LIVE else ""}</div></div>',
                unsafe_allow_html=True)
nifty_frag = st.fragment(run_every=(2.0 if KITE_LIVE else None))(_nifty_frag)

k = st.columns(6)
regime_txt = "RISK-ON 🟢" if regime_bull else "RISK-OFF 🔴"
with k[0]:
    nifty_frag()
static_kpis = [
    ("Regime", regime_txt, "", "g" if regime_bull else "r"),
    ("Active Setups", str(len(active)), "ready", "g"),
    ("Near Setups", str(len(near)), "building", "a"),
    ("Watchlist", str(len(watch)), "eligible", "bl"),
    ("Scanned", str(len([s for s in picked if s in intr])), "of "+str(len(picked)), "mut"),
]
for col,(l,v,sub,cls) in zip(k[1:], static_kpis):
    col.markdown(f'<div class="kpi"><div class="v {cls}">{v}</div><div class="l">{l}</div>'
                 f'<div class="l" style="color:#5b6480">{sub}</div></div>', unsafe_allow_html=True)

st.write("")

# ------------------------------------------------------------------ module-gated tabs
tab_specs = [("Active", f"🎯 Active ({len(active)})"), ("Near", f"⏳ Near ({len(near)})"),
             ("Watchlist", f"👀 Watchlist ({len(watch)})"), ("Closed", f"🔒 Closed ({len(closed_list)})"),
             ("Charts", "📊 Charts"), ("Success", "✅ Success Ratio"), ("Alerts", "🔔 Alerts"),
             ("Full", "📋 Full Scan"), ("Daily", "📅 Daily Summary")]
allowed = set(MY_MODULES)
if IS_ADMIN or "Active" in allowed: allowed.add("Full")
vis = [(mod, lab) for mod, lab in tab_specs if mod in allowed]
if not vis:
    st.error("No modules are assigned to your account. Ask the admin to grant access.")
    st.stop()
tob = st.tabs([lab for _, lab in vis])
T = {mod: tob[i] for i, (mod, _) in enumerate(vis)}

if "Active" in T:
    setup_tab(T["Active"], active, "focus_active", "act",
              "No stocks meet ALL entry conditions on the last closed 15m candle.", allow_pin=True)
if "Near" in T:
    setup_tab(T["Near"], near, "focus_near", "nr", "No near setups right now.",
              sub="Daily side eligible · 15m trigger 1–2 conditions away (confirms on the next 15m close).")
if "Watchlist" in T:
    setup_tab(T["Watchlist"], watch, "focus_watch", "wt", "Watchlist empty.")

if "Closed" in T:
    with T["Closed"]:
        st.markdown("#### 🔒 Closed today — Target 1 or Stop hit in real time")
        if not closed_list:
            st.info("No signals have closed yet today. When a live ACTIVE setup reaches Target 1 "
                    "(or its Stop), it's recorded here and leaves the Active tab.")
        else:
            cw = sum(1 for c in closed_list if c["status"] == "WIN")
            cl = len(closed_list) - cw
            gc = st.columns(3)
            gc[0].markdown(f"<div class='kpi'><div class='v g'>{cw}</div><div class='l'>Target hit</div></div>", unsafe_allow_html=True)
            gc[1].markdown(f"<div class='kpi'><div class='v r'>{cl}</div><div class='l'>Stopped out</div></div>", unsafe_allow_html=True)
            gc[2].markdown(f"<div class='kpi'><div class='v bl'>{cw}/{len(closed_list)}</div><div class='l'>Closed win rate</div></div>", unsafe_allow_html=True)
            dfc = pd.DataFrame([{"Symbol":c["symbol"],"Side":c["side"],"Entry":c["entry"],
                "Exit":c["exit"],"Target":c["t1"],"SL":c["sl"],"Outcome":c["outcome"],
                "Entered":c["entry_ts"],"Closed":c["exit_ts"]} for c in closed_list])
            st.dataframe(dfc, width="stretch", hide_index=True)

if "Charts" in T:
    with T["Charts"]:
        allsyms = sorted(set([r["symbol"] for r in rows] + [s for s in picked if s in intr]))
        opts = [s for s in PINNED if s in allsyms] + [s for s in allsyms if s not in PINNED]
        if not opts:
            st.info("No symbols to chart.")
        else:
            if PINNED:
                st.caption("📌 Priority (pinned) symbols appear first: " + ", ".join(PINNED))
            sel = st.selectbox("Stock", opts, key="charts_sel")
            render_chart(sel, next((x for x in rows if x["symbol"] == sel), None), "charts",
                         preloaded15=intr.get(sel))

if "Success" in T:
    with T["Success"]:
        st.markdown("#### ✅ Backtest Success Ratio — historical win-rate of the current model")
        st.caption("From the standalone daily backtest (`backtest_strategy.py`, 2018→today over every "
                   "large-cap). **WIN** = trade closed in profit. Runs without opening the app — re-run "
                   "the backtest to refresh. This drives the Success ring on every card.")
        BW = sum(w for w, l in BT_SCORES.values())
        BL = sum(l for w, l in BT_SCORES.values())
        BT_TOT = BW + BL
        bt_wr  = BW / BT_TOT * 100 if BT_TOT else 0.0
        g = st.columns(5)
        kk = [("Symbols covered", str(len(BT_SCORES)), "bl"),
              ("Backtested trades", str(BT_TOT), "bl"),
              ("Wins", str(BW), "g"), ("Losses", str(BL), "r"),
              ("Win %", f"{bt_wr:.1f}%", "g" if bt_wr >= 50 else ("a" if bt_wr >= 45 else "r"))]
        for col,(l,v,cls) in zip(g, kk):
            col.markdown(f"<div class='kpi'><div class='v {cls}'>{v}</div><div class='l'>{l}</div></div>",
                         unsafe_allow_html=True)
        if BT_TOT:
            ok = bt_wr >= 50
            st.markdown(f"<div style='margin:14px 0;font-size:20px;font-weight:800;"
                        f"color:{'#00d69e' if ok else '#ffb547'};'>"
                        f"Model win-rate {bt_wr:.1f}%  ({BW} wins vs {BL} losses across "
                        f"{len(BT_SCORES)} stocks)</div>", unsafe_allow_html=True)
            tbl = [{"Symbol": k, "Trades": w+l, "Wins": w, "Losses": l,
                    "Win %": round(w/(w+l)*100, 1)} for k, (w, l) in BT_SCORES.items()]
            dfp = pd.DataFrame(tbl).sort_values(["Win %", "Trades"], ascending=False)
            st.dataframe(dfp, width="stretch", hide_index=True,
                         column_config={"Win %": st.column_config.NumberColumn(format="%.1f%%")})
        else:
            st.info("No backtest results found. Run **`python backtest_strategy.py`** once to generate "
                    "`trades.csv` — per-symbol win-rates then appear here and on every card's Success ring.")

        # ---- live forward-test (today onward) kept as a secondary record ----
        with st.expander("📡 Live forward-test — today's real signals (separate from the backtest)"):
            wins   = [e for e in LOG if e.get("status") == "WIN"]
            losses = [e for e in LOG if e.get("status") == "LOSS"]
            openn  = [e for e in LOG if e.get("status") == "OPEN"]
            ltot   = len(wins) + len(losses)
            lnet   = (len(wins) - len(losses)) / ltot * 100 if ltot else 0.0
            days   = len({e["date"] for e in LOG})
            st.caption(f"{len(wins)} wins · {len(losses)} losses · {len(openn)} open · "
                       f"net {lnet:+.1f}% over {days} day{'s' if days != 1 else ''}. "
                       "Each ACTIVE signal is tracked live against the real price and logged in "
                       "`signal_log.json` — this record starts fresh from today.")

if "Alerts" in T:
    with T["Alerts"]:
        st.markdown("#### 🔔 Create an alert")
        ac = st.columns([2, 2, 1, 2, 1])
        asym = ac[0].text_input("Symbol", key="al_sym").strip().upper()
        amet = ac[1].selectbox("Metric", ["Price","EMA13","EMA50","EMA200","ADX","RSI"], key="al_met")
        aop  = ac[2].selectbox("Cond", [">", "<"], key="al_op")
        aval = ac[3].number_input("Value", value=0.0, step=1.0, key="al_val")
        if ac[4].button("➕ Add", key="al_add", width="stretch"):
            if asym:
                al = st.session_state.setdefault("alerts", [])
                al.append({"id": f"{asym}-{amet}-{aop}-{aval}-{len(al)}", "symbol": asym,
                           "metric": amet, "op": aop, "value": float(aval)})
                st.rerun()
            else:
                st.warning("Type a symbol first.")
        st.markdown("##### Setup notifications")
        sc = st.columns(3)
        st.session_state["notify_active"] = sc[0].toggle("New ACTIVE setup",
            value=st.session_state.get("notify_active", True))
        st.session_state["notify_target"] = sc[1].toggle("Target hit",
            value=st.session_state.get("notify_target", True))
        st.session_state["notify_sl"] = sc[2].toggle("Stop-loss hit",
            value=st.session_state.get("notify_sl", True))
        st.divider()
        al = st.session_state.get("alerts", [])
        cL, cR = st.columns(2)
        with cL:
            st.markdown("##### Your alerts")
            if not al:
                st.caption("No custom alerts yet.")
            for a in list(al):
                r1 = st.columns([5, 1])
                r1[0].write(f"**{a['symbol']}** · {a['metric']} {a['op']} {a['value']}")
                if r1[1].button("🗑", key=f"delal_{a['id']}"):
                    st.session_state["alerts"] = [x for x in al if x["id"] != a["id"]]; st.rerun()
        with cR:
            st.markdown("##### Alert log")
            log = st.session_state.get("alert_log", [])
            if log:
                st.dataframe(pd.DataFrame(log), width="stretch", hide_index=True, height=260)
            else:
                st.caption("Nothing fired yet.")
        st.caption("Alerts fire as in-app toasts **and** browser notifications (click Allow when your "
                   "browser asks). Each alert re-arms automatically once its condition clears.")

if "Full" in T:
    with T["Full"]:
        if not rows:
            st.info("Nothing matched. Try widening thresholds or the full universe.")
        else:
            df = pd.DataFrame([{"Symbol":r["symbol"],"Side":r["side"],"Status":r["status"],
                "Success%": r.get("score") if r.get("score") is not None else np.nan,
                "Price":round(r["price"],1),"Chg%":round(r["chg"],2),
                "Entry":round(r["entry"],1),"SL":round(r["sl"],1),
                "T1":round(r["t1"],1),"T2":round(r["t2"],1),"T3":round(r["t3"],1),
                "ADX":round(r["adx"],1),"RSI":round(r["rsi"],1),"Met":f'{r["met"]}/{r["total"]}'}
                for r in sorted(rows,key=lambda x:({"ACTIVE":0,"NEAR":1,"WATCH":2}[x["status"]],
                                                   -(x.get("score") or 0),-x["met"]))])
            st.dataframe(df, width="stretch", hide_index=True, column_config={
                "Chg%": st.column_config.NumberColumn(format="%.2f%%"),
                "Success%": st.column_config.ProgressColumn(
                    "Live Success", format="%.0f%%", min_value=0, max_value=100,
                    help="Live win-rate of this stock's recorded signals (builds up from today).")})
            st.download_button("⬇ Download scan (CSV)", df.to_csv(index=False),
                               "fno_scan.csv", "text/csv")

if "Daily" in T:
    with T["Daily"]:
        st.markdown("#### 📅 Daily Summary — saved track record (grows each trading day)")
        if not LOG:
            st.info("The track record is empty. Every ACTIVE signal recorded from today onward is "
                    "tracked live and saved to `signal_log.json`; the per-day results appear here.")
        else:
            by_date = {}
            for e in LOG:
                d = by_date.setdefault(e["date"], {"WIN":0,"LOSS":0,"OPEN":0,"EOD":0})
                d[e.get("status","OPEN")] = d.get(e.get("status","OPEN"), 0) + 1
            tw = sum(1 for e in LOG if e["status"]=="WIN"); tl = sum(1 for e in LOG if e["status"]=="LOSS")
            tot = tw+tl; net = (tw-tl)/tot*100 if tot else 0
            g = st.columns(4)
            for col,(l,v,cls) in zip(g, [("Trading days", str(len(by_date)), "bl"),
                    ("Total wins ✅", str(tw), "g"), ("Total losses ❌", str(tl), "r"),
                    ("Overall success", f"{net:+.0f}%", "g" if net>=0 else "r")]):
                col.markdown(f"<div class='kpi'><div class='v {cls}'>{v}</div><div class='l'>{l}</div></div>",
                             unsafe_allow_html=True)
            st.markdown("##### By day")
            rows_d = []
            for dt in sorted(by_date, reverse=True):
                c = by_date[dt]; res = c["WIN"] + c["LOSS"]
                rows_d.append({"Date": dt, "Signals": sum(c.values()), "Wins": c["WIN"], "Losses": c["LOSS"],
                               "Open": c["OPEN"], "Unresolved": c["EOD"],
                               "Success %": round((c["WIN"]-c["LOSS"])/res*100, 1) if res else 0.0})
            st.dataframe(pd.DataFrame(rows_d), width="stretch", hide_index=True,
                         column_config={"Success %": st.column_config.NumberColumn(format="%+.1f%%")})
            st.markdown(f"##### Today's signals · {now_ist().strftime('%d %b %Y (%A)')}")
            todays = [e for e in LOG if e["date"] == today]
            if todays:
                st.dataframe(pd.DataFrame([{"Symbol":e["symbol"],"Side":e["side"],"Entry":e["entry"],
                    "SL":e["sl"],"Target":e["t1"],"Status":e["status"],"Exit":e.get("exit"),
                    "In":e["entry_ts"],"Out":e.get("exit_ts")} for e in todays]),
                    width="stretch", hide_index=True)
            else:
                st.caption("No signals yet today.")
            st.download_button("⬇ Download full track record (CSV)",
                               pd.DataFrame(LOG).to_csv(index=False), "signal_log.csv", "text/csv")

st.caption("⚠️ Live signals use Angel One real-time ticks (+ Yahoo historical bars for indicator "
           "warm-up). Success % is a historical backtest of the model (Target 1 before the stop); "
           "today's real signals are also forward-tracked separately. "
           "Educational/research only — not investment advice. Verify on your broker terminal.")
