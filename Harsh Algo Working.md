# Harsh Algo Working

**Project:** F&O 5-minute options strategy — reverse-engineering, backtesting & research
**Data:** `data_5m/` (213 symbols, 5m OHLCV, 10 Apr–3 Jul 2026, 897,666 bars, verified clean — 0 OHLC violations, 0 duplicate timestamps) · `data/` (209 symbols, daily EOD, 2018–2026)
**Status:** Complete investigation log + code inventory. Backup of the full working session.

---

## 1. The vendor strategy ("Agentr AI Algo") — reverse-engineered logic

Marketing snapshot claimed: 175 triggers → 75 trades (42.9% kept), **78.9% win rate**, 1:1.5 R:R, backtest 1–24 Jun 2026.

Reverse-engineered framework (from the quick-guide + chart screenshots):
- **Levels:** PDH/PDL/PDO/PDC (previous day H/L/O/C) + DO/DH/DL (today developing) + 9 EMA on 5m.
- **Signal:** 5m **close** above PDH → long / below PDL → short, with price on the correct side of the 9 EMA. One per side per day.
- **Averaging:** on pullback to another level that the 5m candle closes back above/below.
- **Exit rule (confirmed from 3 chart screenshots — MOTILALOFS ×1, LUPIN ×1):**
  - **R:R = exactly 1.5** (measured on every labeled trade).
  - **SL is LEVEL-BASED** — sits on a previous-day level (LUPIN: SL 2423.80 = PDH exactly; MOTILALOFS entries gave 0.86% & 1.0%). Effective distance ≈ **0.9%**.
  - **"SL on 5m close · TP on wick touch"** — asymmetric exit: TP fills on any wick touch, SL only on a candle close beyond. Single target (exit fully on TP1 or SL).

---

## 2. Key backtest results (real 5m data)

### 2a. Vendor logic, faithful — with the WRONG (level-distance) SL
Using risk = distance-to-broken-level (tiny/unstable): **56% win, −0.163R** expectancy full period; **54.3% win, −0.188R** in the vendor's own June window (3,125 trades, 40× their sample). **78.9% NOT reproducible.**

### 2b. Vendor logic — with the CORRECTED SL (0.93% level-based, wick-TP/close-SL)
| Metric | Original vendor | Improvised (my filters) |
|---|---|---|
| Win rate | **45.4%** | 44.7% |
| Avg win / loss | 0.98R / −0.90R | 0.92R / −0.89R |
| Expectancy | **−0.045R (near breakeven)** | −0.080R |
| Profit factor | 0.91 | 0.84 |

- Correcting the SL turned a fake −100% wipeout into **near-breakeven**. The wick-TP/close-SL asymmetry is a real win-rate lever.
- **But still slightly negative** — a slow bleed to costs. Win rate ~45% (not 78.9%) because TP (1.39%) sits farther than SL (0.93%).

### 2c. Vendor vs improved (ATR-stop version, earlier run)
Improvements (ATR hard stop, VWAP regime, volume/time filters, breakeven+2R, no averaging) delivered **R:R 2.23:1 vs 0.42:1** and cut avg loss from −2.42R to −0.79R — every mechanical flaw fixed — yet **still net negative**, proving the entry signal itself has no edge.

---

## 3. "Build a better algo" research (all train/test validated)

**Intraday 5m — no robust edge exists.** Tested rigorously:
- Archetypes: VWAP mean-reversion, VWAP-trend pullback, opening-range breakout, momentum — **all negative net of costs**.
- Score deciles: expectancy flat at ≈ −0.2R across all deciles (no quality gradient).
- Hour-of-day: every hour negative.
- Selectivity: only reaches breakeven; global top-3/day was +0.11R on 72 trades = noise.
- Highest-win-rate build (with-trend VWAP reversion): 55–58% win but **0.53:1 R:R** → −0.16R.

**Daily trend-following — the ONE genuine edge (Donchian 55/20, `daily_swing_algo.py`):**
- Long-only: **+0.349R/trade, avg win 2.4R vs loss 0.86R (~2.8:1 R:R)**, ~30% win, defined 2×ATR stop.
- Positive out-of-sample (train 2018–21 +0.195R → test 2022–26 +0.062R).
- **Caveat:** lumpy — profit concentrates in big-trend years (2020, 2023); flat/negative otherwise. Shorts lose (drop them).

---

## 4. Vendor "accuracy" — the selection-bias proof

Second screenshot (LUPIN, 3-Jul) is a **genuine winning trade** — my engine reproduces it (+1.45R). But:
- **LUPIN full period: 34.8% win, −0.099R, net LOSER.** Monthly win swings 20% / 54% / 29% / 50% (pure noise).
- **Per-symbol distribution:** universe median 45.5% win; only 38% of symbols profitable; but ~24 names randomly show 58–64% win in any window.
- **Persistence test (decisive):** correlation(train-exp, test-exp) = **−0.058** (~zero). Top-40 symbols by training (59% win) reverted to 45.7%/−0.026R out-of-sample. Only 49% of profitable names stayed profitable = coin flip.

**Verdict:** The vendor's high-accuracy charts are REAL trades but **hindsight selection on noise.** You cannot pick winning symbols forward. "Accuracy shown" ≠ "accuracy you get."

---

## 5. ₹1,00,000 simulations (delta=1 options, greeks ignored, 2% risk/trade, max 5 concurrent)

| Algo | ₹1,00,000 → today | Note |
|---|---|---|
| Intraday, best high-win build (level-distance SL) | ₹188 (−99.8%) | negative edge wipes account |
| Vendor, **corrected SL** | **₹58,158 (−41.8%)**; OOS June −4.6% | near-breakeven, slow bleed |
| Improvised, corrected SL | ₹38,680 (−61.3%) | filters didn't help here |
| **Daily trend, full 8.5 yr** | **₹2,00,193 (+100%)**, −55% max DD | the only real growth; lumpy |

---

## 6. Bottom-line verdict

1. **The vendor's 78.9% is not reproducible** — real 5m data (incl. their own window) gives ~45%. The screenshots are genuine but hindsight-selected.
2. **Iron law confirmed:** high win rate + high R:R + low risk cannot coexist. Only positive expectancy + defined risk matter.
3. **Intraday directional options trading of these names is net-negative** — no filter, stop, or R:R fix creates edge from a no-edge signal. Corrected mechanic is near-breakeven at best.
4. **Ignoring IV/delta/theta removes the only real options edge** — durable F&O edge is theta/premium SELLING (defined-risk credit spreads / short strangles, ~70–80% win), which needs the greeks.
5. **The realistic path to a positive ₹1L:** (a) daily long-only Donchian trend-following (proven +100%/8.5yr, needs patience through −55% drawdowns), or (b) options-selling backtest (not yet built — offered).

---

## 7. Code inventory (all in project root)

| File | Purpose | Run |
|---|---|---|
| `Agentr_5m_Levels_9EMA.pine` | TradingView indicator: levels + 9EMA + signals + status box + alerts | paste into TradingView Pine Editor |
| `Agentr_5m_Strategy.pine` | TradingView **strategy** (Strategy Tester report) | paste into TradingView |
| `backtest_agentr_5m.py` | Faithful vendor 5m backtest (env: SL_MODE, RISK, START, END) | `python backtest_agentr_5m.py` |
| `backtest_agentr_framework.py` | Daily-analog vendor backtest (weekly levels) | `python backtest_agentr_framework.py` |
| `research_better_algo.py` | 4 intraday archetypes, train/test | `python research_better_algo.py` |
| `research_selectivity.py` | Score-decile / hour / selectivity analysis | `python research_selectivity.py` |
| `intraday_options_algo.py` | Highest-win-rate intraday build + ₹1L sim | `python intraday_options_algo.py` |
| `compare_vendor_vs_improved.py` | A/B: vendor vs improved (ATR-stop version) | `python compare_vendor_vs_improved.py` |
| `reevaluate_image_sl.py` | **A/B with CORRECTED image-based SL** (0.93%, wick-TP/close-SL) | `python reevaluate_image_sl.py` |
| `per_symbol_accuracy.py` | Per-symbol win-rate distribution | `python per_symbol_accuracy.py` |
| `symbol_persistence.py` | **Decisive** train→test persistence test | `python symbol_persistence.py` |
| `daily_swing_algo.py` | **Donchian 55/20 daily trend system (the working edge)** | `python daily_swing_algo.py` |
| `download_nse_5m.py` | 5m data downloader (Yahoo) | `python download_nse_5m.py` |

**Output CSVs:** `agentr_5m_trades.csv`, `intraday_option_trades.csv`, `swing_trades.csv`, `per_symbol_accuracy.csv`, `symbol_persistence.csv`, `best_algo_trades.csv`.

**Note on encoding:** run with `$env:PYTHONIOENCODING="utf-8"` on Windows PowerShell to avoid ₹-symbol console errors.

---

*Backup generated 6 Jul 2026. Full reasoning and per-run numbers are documented above; re-run any script to reproduce.*
