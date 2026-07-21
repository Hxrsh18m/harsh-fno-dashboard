"""
live_feed.py  -  broker-agnostic real-time tick engine for the dashboard.

Streamlit reruns the whole script on every interaction, so a persistent
WebSocket cannot live in the script body. Instead a single LiveFeed object
runs a background daemon thread that streams ticks into a thread-safe store;
the UI (cached via st.cache_resource) just reads the latest snapshot.

Modes
  'off'       -> no streaming; UI uses delayed Yahoo bars only
  'simulate'  -> realistic random-walk ticks seeded from Yahoo (no creds, testable)
  'kite'      -> REAL Zerodha KiteTicker WebSocket (needs api_key + access_token
                 and `pip install kiteconnect`).  Other brokers = one small adapter.

Ticks are last-traded-price only; indicators (EMA/RSI/ADX) still come from the
historical bars — a websocket streams prices, not history.
"""
import threading
import time
import random

# NSE symbol -> Yahoo ticker (for the no-login Yahoo live-polling feed)
_YT_OVERRIDES = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
                 "MIDCPNIFTY": "NIFTY_MID_SELECT.NS", "MCDOWELL-N": "UNITDSPR.NS",
                 "BIRLASOFT": "BSOFT.NS", "PEL": "PIRAMALFIN.NS", "TATAMOTORS": "TMCV.NS"}
def _yt(sym):
    return _YT_OVERRIDES.get(sym.upper(), f"{sym}.NS")


class LiveFeed:
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.ticks = {}          # sym -> (ltp, prev_close, epoch_ts)
        self._cur = {}           # sym -> forming 15m candle (built from ticks)
        self._bars = {}          # sym -> list of completed (bucket, o,h,l,c,vol) candles
        self.mode = "off"
        self.status = "off"      # off | connecting | live | live-demo | error:...
        self.symbols = ()
        self._cfg = None
        self._creds = None
        self._seed = {}          # sym -> (price, prev_close)
        self._ws = None          # active websocket handle (for clean close)

    # ------------------------------------------------------------------ lifecycle
    def configure(self, mode, symbols, seed, creds=None):
        """Idempotent: (re)start the stream only when mode/symbols change."""
        with self._lock:
            for s, pp in seed.items():
                price, prev = (pp if isinstance(pp, (tuple, list)) else (pp, pp))
                if price and s not in self.ticks:
                    self.ticks[s] = (price, prev or price, time.time())
            self._seed = {s: (pp if isinstance(pp, (tuple, list)) else (pp, pp))
                          for s, pp in seed.items()}
            self._creds = creds
        cfg = (mode, tuple(sorted(symbols)))
        if cfg == self._cfg and self._thread and self._thread.is_alive():
            self.mode = mode
            return
        self.stop()
        self._cfg, self.mode, self.symbols = cfg, mode, tuple(symbols)
        if mode == "off":
            self.status = "off"
            return
        self._stop.clear()
        target = {"simulate": self._run_sim, "kite": self._run_kite,
                  "angel": self._run_angel, "yahoo": self._run_yahoo}.get(mode, self._run_yahoo)
        self._thread = threading.Thread(target=target, daemon=True, name="live-feed")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ws is not None:
            try: self._ws.close_connection()
            except Exception:
                try: self._ws.close()
                except Exception: pass
            self._ws = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    # ------------------------------------------------------------------ reads
    def get(self, sym):
        with self._lock:
            v = self.ticks.get(sym)
        if not v:
            return None
        ltp, prev, ts = v
        return {"ltp": ltp, "prev": prev,
                "chg": (ltp / prev - 1) * 100 if prev else 0.0, "ts": ts}

    def last_update(self):
        with self._lock:
            return max((v[2] for v in self.ticks.values()), default=0)

    # ------------------------------------------------------------------ tick -> 15m candles
    def _feed_candle(self, sym, price, cum_vol, ts_sec):
        """Aggregate a tick into the forming 15m candle; finalise on 15-min boundary."""
        bucket = int(ts_sec // 900) * 900          # 15-min bucket start (epoch); aligns to NSE 9:15,9:30…
        with self._lock:
            cur = self._cur.get(sym)
            if cur is None or cur["b"] != bucket:
                if cur is not None and cur["b"] < bucket:      # finalise the completed candle
                    lst = self._bars.setdefault(sym, [])
                    lst.append((cur["b"], cur["o"], cur["h"], cur["l"], cur["c"],
                                max(0.0, cur["ve"] - cur["vs"])))
                    if len(lst) > 250:
                        self._bars[sym] = lst[-250:]
                self._cur[sym] = {"b": bucket, "o": price, "h": price, "l": price, "c": price,
                                  "vs": cum_vol or 0.0, "ve": cum_vol or 0.0}
            else:
                cur["h"] = max(cur["h"], price); cur["l"] = min(cur["l"], price); cur["c"] = price
                if cum_vol:
                    cur["ve"] = cum_vol

    def get_new_candles(self, sym):
        """Completed 15m candles built from live ticks (DataFrame like Yahoo's, IST-naive index)."""
        with self._lock:
            bars = list(self._bars.get(sym, []))
        if not bars:
            return None
        try:
            import pandas as pd
            idx = pd.to_datetime([b[0] for b in bars], unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
            return pd.DataFrame({"Open":[b[1] for b in bars], "High":[b[2] for b in bars],
                                 "Low":[b[3] for b in bars], "Close":[b[4] for b in bars],
                                 "Volume":[b[5] for b in bars]}, index=idx)
        except Exception:
            return None

    # ------------------------------------------------------------------ simulate
    def _run_sim(self):
        self.status = "live-demo"
        rng = random.Random(2025)
        while not self._stop.is_set():
            with self._lock:
                for s in self.symbols:
                    cur = self.ticks.get(s)
                    if not cur:
                        pp = self._seed.get(s)
                        if pp:
                            self.ticks[s] = (pp[0], pp[1] or pp[0], time.time())
                        continue
                    ltp, prev, _ = cur
                    step = rng.gauss(0, 0.0006) * ltp          # ~0.06% std per tick
                    self.ticks[s] = (max(0.05, ltp + step), prev, time.time())
            self._stop.wait(1.0)

    # ------------------------------------------------------------------ Yahoo live-poll (NO LOGIN)
    def _run_yahoo(self):
        """Poll Yahoo's latest 1-minute price for every symbol so the board 'ticks'
        with no login. Yahoo NSE data is ~15 min delayed but updates through the session."""
        try:
            import yfinance as yf
            import pandas as pd
        except Exception:
            self.status = "error: yfinance not installed"
            return
        yts = [_yt(s) for s in self.symbols]
        poll = max(12, len(self.symbols) // 8)                 # scale interval to universe size
        self.status = "live-yahoo"
        while not self._stop.is_set():
            try:
                # threads=False: avoid repeated thread-pool churn (crashes Python 3.14's semaphore)
                data = yf.download(yts, period="1d", interval="1m", group_by="ticker",
                                   progress=False, threads=False, auto_adjust=False)
                if data is not None and len(data):
                    multi = isinstance(data.columns, pd.MultiIndex)
                    with self._lock:
                        for s, yt in zip(self.symbols, yts):
                            try:
                                col = (data[yt]["Close"] if multi else data["Close"]).dropna()
                                if len(col) == 0:
                                    continue
                                price = float(col.iloc[-1])
                                prev = self._seed.get(s, (price, price))[1] or price
                                self.ticks[s] = (price, prev, time.time())
                            except Exception:
                                continue
                self.status = "live-yahoo"
            except Exception as e:
                self.status = f"live-yahoo (retrying: {str(e)[:40]})"
            self._stop.wait(poll)

    # ------------------------------------------------------------------ Zerodha Kite (REAL)
    @staticmethod
    def _nse_symbol(s):
        idx = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
               "FINNIFTY": "NIFTY FIN SERVICE"}
        return idx.get(s.upper(), s.upper())

    def _run_kite(self):
        try:
            from kiteconnect import KiteConnect, KiteTicker
        except Exception:
            self.status = "error: `pip install kiteconnect`"
            return
        creds = self._creds or {}
        api_key, token = creds.get("api_key"), creds.get("access_token")
        if not api_key or not token:
            self.status = "error: missing api_key / access_token"
            return
        try:
            self.status = "connecting"
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(token)
            wanted = {self._nse_symbol(s): s for s in self.symbols}
            tok2sym = {}
            for it in kite.instruments("NSE"):
                if it["tradingsymbol"] in wanted:
                    tok2sym[it["instrument_token"]] = wanted[it["tradingsymbol"]]
            tokens = list(tok2sym.keys())
            if not tokens:
                self.status = "error: no instrument tokens matched"
                return

            kt = KiteTicker(api_key, token)

            def on_ticks(ws, ticks):
                with self._lock:
                    for t in ticks:
                        s = tok2sym.get(t.get("instrument_token"))
                        ltp = t.get("last_price")
                        if not s or not ltp:
                            continue
                        prev = (t.get("ohlc") or {}).get("close") \
                            or self.ticks.get(s, (ltp, ltp))[1]
                        self.ticks[s] = (ltp, prev, time.time())

            def on_connect(ws, resp):
                ws.subscribe(tokens)
                ws.set_mode(ws.MODE_FULL, tokens)
                self.status = "live"

            def on_close(ws, code, reason):
                self.status = f"closed: {reason}"

            def on_error(ws, code, reason):
                self.status = f"error: {reason}"

            kt.on_ticks, kt.on_connect = on_ticks, on_connect
            kt.on_close, kt.on_error = on_close, on_error
            kt.connect(threaded=True)                 # runs its own ws thread
            while not self._stop.is_set():
                self._stop.wait(1.0)
            try:
                kt.close()
            except Exception:
                pass
        except Exception as e:
            self.status = f"error: {e}"

    # ------------------------------------------------------------------ Angel One SmartAPI (FREE, real)
    # NSE indices: match by SYMBOL (Angel stores name='NIFTY', symbol='Nifty 50'); token fallback.
    _IDX_SYM = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
                "FINNIFTY": "NIFTY FIN SERVICE", "MIDCPNIFTY": "NIFTY MID SELECT"}
    _IDX_TOK = {"NIFTY": "26000", "BANKNIFTY": "26009", "FINNIFTY": "26037", "MIDCPNIFTY": "26074"}

    def _run_angel(self):
        try:
            from SmartApi import SmartConnect
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
            import pyotp, urllib.request, json as _json
        except Exception:
            self.status = "error: pip install smartapi-python pyotp logzero"
            return
        c = self._creds or {}
        api_key = c.get("api_key"); client = c.get("client_code")
        pin = c.get("pin"); totp_secret = c.get("totp_secret")
        if not all([api_key, client, pin, totp_secret]):
            self.status = "error: missing Angel credentials"
            return
        try:
            self.status = "connecting"
            obj = SmartConnect(api_key=api_key)
            sess = obj.generateSession(client, pin, pyotp.TOTP(totp_secret).now())
            if not sess or not sess.get("status"):
                self.status = f"error: login failed ({(sess or {}).get('message','')})"
                return
            auth_token = sess["data"]["jwtToken"]
            feed_token = obj.getfeedToken()

            # --- map our symbols -> NSE instrument tokens via the scrip master ---
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            master = _json.loads(urllib.request.urlopen(url, timeout=45).read().decode())
            by_eq = {}
            for it in master:
                if it.get("exch_seg") != "NSE":
                    continue
                by_eq[str(it.get("symbol", "")).upper()] = it.get("token")
            tok2sym, tokens = {}, []
            for s in self.symbols:
                su = s.upper()
                if su in self._IDX_SYM:                       # index: by symbol, then token fallback
                    tok = by_eq.get(self._IDX_SYM[su]) or self._IDX_TOK.get(su)
                else:                                          # equity: SYMBOL-EQ
                    tok = by_eq.get(su + "-EQ")
                if tok:
                    tok2sym[str(tok)] = s; tokens.append(str(tok))
            if not tokens:
                self.status = "error: no instrument tokens matched"
                return

            sws = SmartWebSocketV2(auth_token, api_key, client, feed_token)
            self._ws = sws

            def on_data(wsapp, message):
                try:
                    tk = str(message.get("token", "")).strip('"')
                    s = tok2sym.get(tk)
                    ltp = message.get("last_traded_price")
                    if not s or ltp is None:
                        return
                    price = ltp / 100.0                       # Angel sends paise
                    cp = message.get("closed_price") or message.get("close_price")
                    prev = (cp / 100.0) if cp else self.ticks.get(s, (price, price))[1]
                    with self._lock:
                        self.ticks[s] = (price, prev, time.time())
                    # build the live 15m candle from this tick (Quote mode gives volume + exch time)
                    cum_vol = message.get("volume_trade_for_the_day") or 0
                    ts_ms = message.get("exchange_timestamp") or 0
                    self._feed_candle(s, price, float(cum_vol), (ts_ms / 1000.0) if ts_ms else time.time())
                except Exception:
                    pass

            def on_open(wsapp):
                # mode 2 = QUOTE (LTP + volume + exchange timestamp) so we can build candles
                sws.subscribe("hxfno", 2, [{"exchangeType": 1, "tokens": tokens}])
                self.status = "live"

            def on_error(wsapp, error):
                self.status = f"error: {error}"

            def on_close(wsapp):
                self.status = "closed"

            sws.on_open = on_open
            sws.on_data = on_data
            sws.on_error = on_error
            sws.on_close = on_close
            sws.connect()                                 # blocking; ends when close_connection() called
        except Exception as e:
            self.status = f"error: {e}"
