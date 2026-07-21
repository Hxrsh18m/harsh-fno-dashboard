"""
Backtest: 5/43/200-EMA + RSI(W/M) bands + Bollinger + ADX strategy on NSE F&O
stocks (daily data). Runs stand-alone — no dashboard needed.

Usage:  python backtest_strategy.py
Needs:  ./data/*.csv from yahoo_data_downloader.py
        ./data/NIFTY.csv (auto-downloaded via yfinance if missing)
        market_cap.py   (curated >= Rs 40,000 cr universe gate)

RULES  (daily side; other aspects unchanged)
  Universe         : market cap >= Rs 40,000 cr only (curated list in market_cap.py)
  Long (CE proxy)  : EMA5>EMA43, Close>EMA200, 60<weekly RSI<80, 60<monthly RSI<80,
                     Close NOT above upper Bollinger(20,2), Volume > 20D avg,
                     Nifty close > Nifty 50EMA, ADX filter
  Short (PE proxy) : EMA5<EMA43, Close<EMA200, weekly RSI<40, monthly RSI<50,
                     Close NOT below lower Bollinger(20,2), Volume > 20D avg,
                     Nifty below 50EMA, ADX filter
  ADX filter       : ADX>25 and not falling 2 consecutive days,
                     OR 20<=ADX<=25 and rising
  Signal on close -> trade executed at NEXT day's open
  Exit             : Long when close < EMA13 ; Short when close > EMA13 (next open)
  Sizing           : Rs 1,00,000 capital, Rs 25,000 max per position, max 4 open,
                     whole shares. Competing signals ranked by ADX (highest first).
Fresh start: any previous trades.csv / equity_curve.csv is archived to
             _old_backtest_backup/ before the run, so results always start clean.
Outputs: trades.csv, equity_curve.csv, console performance report.
"""

import os, glob, shutil
from datetime import datetime
import numpy as np
import pandas as pd
from market_cap import is_large_cap

DATA_DIR   = "data"
NIFTY_FILE = os.path.join(DATA_DIR, "NIFTY.csv")
BB_LEN     = 20            # Bollinger Band length
BB_STD     = 2.0          # Bollinger Band width (std devs)
CAPITAL    = 100_000
SL_PCT     = 0.15          # 15% stop-loss on entry price (intraday trigger)
ALLOC      = 25_000
MAX_POS    = 4
RSI_LEN    = 14
ADX_LEN    = 14
MIN_BARS   = 260           # skip symbols with too little history for warmup

# ------------------------- indicators -------------------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(close, n=RSI_LEN):
    d  = close.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def adx(df, n=ADX_LEN):
    h, l, c = df["High"], df["Low"], df["Close"]
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr  = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * pdm.ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan)
    mdi = 100 * mdm.ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan)
    dx  = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()

def htf_rsi(close, rule):
    """RSI on COMPLETED weekly/monthly bars only (shift 1), ffilled to daily. No lookahead."""
    bars = close.resample(rule).last().dropna()
    return rsi(bars).shift(1).reindex(close.index, method="ffill")

# ------------------------- data -------------------------
def load_csv(path):
    df = pd.read_csv(path, parse_dates=["Date"], index_col="Date").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    # yfinance CSVs are numeric already, but coerce defensively (stray header rows -> NaN)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(df.columns):
        return None
    return df.dropna(subset=["Open", "High", "Low", "Close"])

def get_nifty():
    if os.path.exists(NIFTY_FILE):
        return load_csv(NIFTY_FILE)
    import yfinance as yf
    df = yf.download("^NSEI", start="2015-01-01", interval="1d",
                     auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index.name = "Date"
    df.to_csv(NIFTY_FILE)
    return load_csv(NIFTY_FILE)

# ------------------------- per-symbol signal table -------------------------
def prepare(df, regime_daily):
    c = df["Close"]
    e5, e43, e200 = ema(c, 5), ema(c, 43), ema(c, 200)
    e13 = ema(c, 13)                 # kept only for the EMA13 exit (unchanged)
    a = adx(df)
    rising  = a > a.shift(1)
    fell2   = (a < a.shift(1)) & (a.shift(1) < a.shift(2))
    adx_ok  = ((a > 25) & ~fell2) | (a.between(20, 25) & rising)

    vol_ok  = df["Volume"] > df["Volume"].rolling(20).mean()
    rsi_w   = htf_rsi(c, "W-FRI")
    rsi_m   = htf_rsi(c, "ME")
    regime  = regime_daily.reindex(df.index, method="ffill")

    # Bollinger Band(20, 2) on daily close
    bb_mid = c.rolling(BB_LEN).mean()
    bb_sd  = c.rolling(BB_LEN).std()
    bb_up  = bb_mid + BB_STD * bb_sd
    bb_lo  = bb_mid - BB_STD * bb_sd

    # long/short trend gate: 5EMA above/below 43EMA + price above/below 200EMA
    trend_up = (e5 > e43) & (c > e200)
    trend_dn = (e5 < e43) & (c < e200)

    t = pd.DataFrame(index=df.index)
    t["open"]  = df["Open"]          # execution price for today
    t["close"] = c                   # mark-to-market
    t["high"], t["low"] = df["High"], df["Low"]   # for intraday SL trigger
    # signals generated at PREVIOUS close -> shift(1) so row = action day
    long_sig  = (trend_up & (rsi_w > 60) & (rsi_w < 80) & (rsi_m > 60) & (rsi_m < 80)
                 & (c <= bb_up) & vol_ok & adx_ok & (regime == 1))
    short_sig = (trend_dn & (rsi_w < 40) & (rsi_m < 50)
                 & (c >= bb_lo) & vol_ok & adx_ok & (regime == -1))
    t["enter_long"]  = long_sig.shift(1, fill_value=False)
    t["enter_short"] = short_sig.shift(1, fill_value=False)
    t["exit_long"]   = (c < e13).shift(1, fill_value=False)
    t["exit_short"]  = (c > e13).shift(1, fill_value=False)
    t["adx_rank"]    = a.shift(1)    # for ranking competing entries
    return t

# ------------------------- fresh start -------------------------
def clear_old_backtest():
    """Archive any previous backtest outputs so every run starts from a clean slate."""
    stale = [f for f in ("trades.csv", "equity_curve.csv") if os.path.exists(f)]
    if not stale:
        return
    backup = "_old_backtest_backup"
    os.makedirs(backup, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in stale:
        shutil.move(f, os.path.join(backup, f"{stamp}_{f}"))
    print(f"Cleared {len(stale)} old backtest file(s) -> {backup}/ (fresh start)\n")

# ------------------------- engine -------------------------
def run():
    clear_old_backtest()
    nifty = get_nifty()
    if nifty is None or nifty.empty:
        raise SystemExit("Could not load NIFTY.csv")
    regime = pd.Series(np.where(nifty["Close"] > ema(nifty["Close"], 50), 1, -1),
                       index=nifty.index)

    tables, skipped_cap = {}, 0
    for f in sorted(glob.glob(os.path.join(DATA_DIR, "*.csv"))):
        sym = os.path.splitext(os.path.basename(f))[0]
        if sym in ("NIFTY", "failed_symbols"):
            continue
        if not is_large_cap(sym):        # market-cap >= Rs 40,000 cr gate
            skipped_cap += 1
            continue
        df = load_csv(f)
        if df is None or len(df) < MIN_BARS:
            continue
        tables[sym] = prepare(df, regime)
    if not tables:
        raise SystemExit("No usable CSVs in ./data")
    print(f"Loaded {len(tables)} large-cap symbols  ({skipped_cap} skipped: mktcap < Rs 40,000 cr)\n")

    all_days = sorted(set().union(*[t.index for t in tables.values()]))
    cash, positions, trades, curve = float(CAPITAL), {}, [], []

    for day in all_days:
        # ---- 1) exits at today's open ----
        for sym in list(positions):
            t = tables[sym]
            if day not in t.index:
                continue
            p, row = positions[sym], t.loc[day]
            px, reason = None, None

            # 1) stop-loss: intraday trigger, gap-through fills at open
            if p["side"] == "L":
                stop = p["entry_px"] * (1 - SL_PCT)
                if row["open"] <= stop:
                    px, reason = row["open"], "SL_gap"
                elif row["low"] <= stop:
                    px, reason = stop, "SL"
            else:
                stop = p["entry_px"] * (1 + SL_PCT)
                if row["open"] >= stop:
                    px, reason = row["open"], "SL_gap"
                elif row["high"] >= stop:
                    px, reason = stop, "SL"

            # 2) EMA13 signal exit (at open, only if SL not already hit)
            if px is None and ((p["side"] == "L" and row["exit_long"]) or
                               (p["side"] == "S" and row["exit_short"])):
                px, reason = row["open"], "EMA13"

            if px is not None:
                pnl = (px - p["entry_px"]) * p["qty"] * (1 if p["side"] == "L" else -1)
                cash += p["cost"] + pnl
                trades.append({"symbol": sym, "side": p["side"],
                               "entry_date": p["entry_date"], "entry_px": p["entry_px"],
                               "exit_date": day, "exit_px": round(px, 2),
                               "qty": p["qty"], "pnl": round(pnl, 2),
                               "exit_reason": reason,
                               "days_held": (day - p["entry_date"]).days})
                del positions[sym]

        # ---- 2) entries at today's open, ranked by ADX ----
        slots = MAX_POS - len(positions)
        if slots > 0:
            cands = []
            for sym, t in tables.items():
                if sym in positions or day not in t.index:
                    continue
                row = t.loc[day]
                if bool(row["enter_long"]):
                    cands.append((row["adx_rank"], sym, "L", row["open"]))
                elif bool(row["enter_short"]):
                    cands.append((row["adx_rank"], sym, "S", row["open"]))
            # rank by ADX desc; NaN ADX sinks to the bottom, symbol name breaks ties
            cands.sort(key=lambda x: (-np.inf if pd.isna(x[0]) else x[0], x[1]), reverse=True)
            for adx_v, sym, side, px in cands[:slots]:
                if pd.isna(px) or px <= 0:
                    continue
                budget = min(ALLOC, cash)
                if budget <= 0:
                    continue
                qty = int(budget // px)
                if qty == 0:
                    continue
                cost = qty * px            # short margin treated same as long cost (conservative)
                cash -= cost
                positions[sym] = {"side": side, "entry_date": day,
                                  "entry_px": round(px, 2), "qty": qty, "cost": cost}

        # ---- 3) mark to market ----
        mtm = cash
        for sym, p in positions.items():
            t = tables[sym]
            px = t.loc[day, "close"] if day in t.index else p["entry_px"]
            mtm += p["cost"] + (px - p["entry_px"]) * p["qty"] * (1 if p["side"] == "L" else -1)
        curve.append({"date": day, "equity": round(mtm, 2), "open_positions": len(positions)})

    # ---- close any remaining open positions at last close ----
    for sym, p in list(positions.items()):
        t = tables[sym]
        px = t["close"].iloc[-1]
        pnl = (px - p["entry_px"]) * p["qty"] * (1 if p["side"] == "L" else -1)
        cash += p["cost"] + pnl
        trades.append({"symbol": sym, "side": p["side"], "entry_date": p["entry_date"],
                       "entry_px": p["entry_px"], "exit_date": t.index[-1],
                       "exit_px": round(px, 2), "qty": p["qty"], "pnl": round(pnl, 2),
                       "exit_reason": "EOD_data", "days_held": (t.index[-1] - p["entry_date"]).days})

    report(trades, curve)

# ------------------------- report -------------------------
def report(trades, curve):
    tr = pd.DataFrame(trades)
    eq = pd.DataFrame(curve).set_index("date")
    tr.to_csv("trades.csv", index=False)
    eq.to_csv("equity_curve.csv")

    if tr.empty:
        print("No trades generated. Check data length (need EMA200 + monthly RSI warmup).")
        return

    wins  = tr[tr.pnl > 0]
    loss  = tr[tr.pnl <= 0]
    final = eq["equity"].iloc[-1]
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr  = (final / CAPITAL) ** (1 / years) - 1 if final > 0 else float("nan")
    dd    = (eq["equity"] / eq["equity"].cummax() - 1).min()
    pf    = wins.pnl.sum() / abs(loss.pnl.sum()) if len(loss) and loss.pnl.sum() != 0 else float("inf")

    print("=" * 46)
    print("PERFORMANCE REPORT")
    print("=" * 46)
    print(f"Period            : {eq.index[0].date()} -> {eq.index[-1].date()}")
    print(f"Start / End equity: {CAPITAL:,.0f} / {final:,.0f}")
    print(f"Total return      : {final/CAPITAL-1: .2%}")
    print(f"CAGR              : {cagr: .2%}")
    print(f"Max drawdown      : {dd: .2%}")
    print(f"Trades            : {len(tr)}  (L:{(tr.side=='L').sum()}  S:{(tr.side=='S').sum()})")
    print(f"Win rate          : {len(wins)/len(tr): .1%}")
    print(f"Avg win / loss    : {wins.pnl.mean():,.0f} / {loss.pnl.mean():,.0f}" if len(loss) else f"Avg win: {wins.pnl.mean():,.0f}")
    print(f"Expectancy/trade  : {tr.pnl.mean():,.0f}")
    print(f"Profit factor     : {pf:.2f}")
    print(f"Avg days held     : {tr.days_held.mean():.1f}")
    print(f"Exit breakdown    : {tr.exit_reason.value_counts().to_dict()}")
    print("\nSaved: trades.csv, equity_curve.csv")

if __name__ == "__main__":
    run()
