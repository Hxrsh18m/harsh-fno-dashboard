"""
Market-cap universe gate for the Harsh FnO model.

WHY THIS FILE EXISTS
--------------------
The OHLC data used by the screener/backtest has NO market-cap column, so a
market-cap filter has to come from a curated list. This module is that list,
kept in ONE place and imported by dashboard.py, dashboard_live.py and
backtest_strategy.py so all three stay consistent.

RULE: only F&O names with market cap >= Rs 40,000 crore are tradable.

The set below is a HAND-CURATED estimate (~early-mid 2026). It is meant to be
edited freely — add a symbol to include it, remove one to drop it. Borderline
names (roughly Rs 35,000-42,000 cr) were left OUT to stay conservative; move any
of them into LARGECAP_40K if you want them in. Symbols NOT in this set are
excluded from every scan and backtest.
"""

# Minimum market cap to trade (documentation / single source of truth).
MIN_MARKET_CAP_CR = 40_000

# --- F&O names with market cap >= Rs 40,000 cr (curated estimate — EDIT FREELY) ---
LARGECAP_40K = {
    "ABB", "ACC", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS", "ALKEM",
    "AMBUJACEM", "APLAPOLLO", "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT", "ATGL",
    "AUBANK", "AUROPHARMA", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE",
    "BALKRISIND", "BANKBARODA", "BANKINDIA", "BEL", "BERGEPAINT", "BHARATFORG",
    "BHARTIARTL", "BHEL", "BIOCON", "BOSCHLTD", "BPCL", "BRITANNIA", "BSE",
    "CANBK", "CHOLAFIN", "CIPLA", "COALINDIA", "COFORGE", "COLPAL", "CONCOR",
    "COROMANDEL", "CUMMINSIND", "DABUR", "DIVISLAB", "DIXON", "DLF", "DRREDDY",
    "EICHERMOT", "ESCORTS", "ETERNAL", "FEDERALBNK", "GAIL", "GLENMARK",
    "GMRAIRPORT", "GODREJCP", "GODREJPROP", "GRASIM", "HAL", "HAVELLS",
    "HCLTECH", "HDFCAMC", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO",
    "HINDPETRO", "HINDUNILVR", "ICICIBANK", "ICICIGI", "ICICIPRULI", "IDEA",
    "IDFCFIRSTB", "INDHOTEL", "INDIANB", "INDIGO", "INDUSINDBK", "INFY", "IOC",
    "IPCALAB", "IRCTC", "IRFC", "ITC", "JINDALSTEL", "JIOFIN", "JSWSTEEL",
    "JUBLFOOD", "KALYANKJIL", "KOTAKBANK", "KPITTECH", "LICI", "LT", "LTF",
    "LTTS", "LUPIN", "M&M", "MARICO", "MARUTI", "MCDOWELL-N", "MFSL",
    "MOTHERSON", "MPHASIS", "MRF", "MUTHOOTFIN", "NAUKRI", "NESTLEIND", "NMDC",
    "NTPC", "NYKAA", "OBEROIRLTY", "OFSS", "ONGC", "PAGEIND", "PAYTM",
    "PERSISTENT", "PETRONET", "PFC", "PIDILITIND", "PIIND", "PNB", "POLYCAB",
    "POWERGRID", "PRESTIGE", "RECLTD", "RELIANCE", "RVNL", "SAIL", "SBICARD",
    "SBILIFE", "SBIN", "SHREECEM", "SHRIRAMFIN", "SIEMENS", "SJVN", "SRF",
    "SUNPHARMA", "SUPREMEIND", "TATACOMM", "TATACONSUM", "TATAMOTORS",
    "TATAPOWER", "TATASTEEL", "TCS", "TECHM", "TIINDIA", "TITAN", "TORNTPHARM",
    "TORNTPOWER", "TRENT", "TVSMOTOR", "UBL", "ULTRACEMCO", "UNIONBANK", "UPL",
    "VEDL", "VOLTAS", "WIPRO", "YESBANK", "ZYDUSLIFE",
}

# Index symbols are never stocks — always allowed through (used for regime, not traded).
_INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX"}


def is_large_cap(symbol):
    """True if `symbol` is an index or a curated >= Rs 40,000 cr F&O name."""
    s = str(symbol).strip().upper()
    return s in _INDICES or s in LARGECAP_40K


def filter_universe(symbols):
    """Keep only large-cap (>= Rs 40,000 cr) names from an iterable of symbols."""
    return [s for s in symbols if is_large_cap(s)]
