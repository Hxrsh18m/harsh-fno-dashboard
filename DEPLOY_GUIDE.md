# Put "Harsh FnO API 1.0" online (free, always-on)  —  no coding needed

You will host it on **Streamlit Community Cloud** (free). It gives you a permanent
web link like `https://harshfno.streamlit.app` that works 24/7, even when your PC is off.
Total time: about 10–15 minutes, all done by clicking in your web browser.

Your files are in this folder:
`C:\Users\harsh\OneDrive\Desktop\Python Dashboard FnO`

**🔒 The app has a login.** The password is NOT stored in the code — it is read from
Streamlit **Secrets**, so this repo can safely be public. Set it at deploy time:
in Streamlit Cloud → **Advanced settings → Secrets**, add:
```toml
[auth]
password = "your_password_here"
```
Then sign in with **Username:** `Hxrsh` and that password. (See the last section to change it.)

---

## STEP 1 — Make a free GitHub account (your files live here)
1. Open your browser and go to  **https://github.com/signup**
2. Enter your email, a password, and a username (e.g. `harshfno`). Verify your email.
   (GitHub is just free online storage for the app files — you won't write any code.)

## STEP 2 — Create an empty project ("repository")
1. After logging in, click the **`+`** at the top-right → **New repository**.
2. Repository name: `harsh-fno-dashboard`
3. Choose **Private** (recommended — this keeps your login password out of public view;
   Streamlit's free plan works fine with private repos). Click **Create repository**.

## STEP 3 — Upload the 4 app files
1. On the new repository page, click **Add file** → **Upload files**.
2. Open your folder `C:\Users\harsh\OneDrive\Desktop\Python Dashboard FnO`.
3. Select and drag **these files** into the upload area in the browser:
   - `dashboard_live.py`
   - `live_feed.py`
   - `market_cap.py`      ← market-cap (≥ ₹40,000 cr) universe list
   - `requirements.txt`
   - `Options Symbols.csv`
   - `trades.csv`         ← backtest results that power the Success % ring
4. Click the green **Commit changes** button.

## STEP 4 — (Optional but recommended) add the dark theme
1. Click **Add file** → **Create new file**.
2. In the file-name box, type exactly:  `.streamlit/config.toml`
   (typing the `/` automatically makes the folder)
3. Paste these lines into the big text box:
   ```
   [theme]
   base = "dark"
   primaryColor = "#00C805"
   backgroundColor = "#0B0E14"
   secondaryBackgroundColor = "#141924"
   textColor = "#E6E9EF"
   ```
4. Click **Commit changes**.

## STEP 5 — Deploy on Streamlit (this creates your live link)
1. Go to  **https://share.streamlit.io**
2. Click **Sign in** → **Continue with GitHub** → **Authorize**.
3. Click **Create app** → choose **Deploy a public app from GitHub**.
4. Fill in:
   - **Repository:** `harshfno/harsh-fno-dashboard` (your username/repo)
   - **Branch:** `main`
   - **Main file path:** `dashboard_live.py`
5. (Optional) Click **Advanced** and set the app URL name, e.g. `harshfno`
   → your link becomes `https://harshfno.streamlit.app`.
6. Click **Deploy**. Wait 2–4 minutes while it installs everything.
7. Done — your dashboard is now LIVE on the internet. Open the link on your phone,
   laptop, anywhere. Share it with anyone.

---

## Everyday things you'll want to know

**Update the app later (change a setting, add symbols, etc.)**
- Just re-upload the changed file to the GitHub repo (Add file → Upload files → drag →
  Commit). Streamlit re-deploys automatically in ~2 minutes. No need to redo anything.

**Make it private (only you / chosen people can view)**
- In Streamlit Cloud, open your app → **Settings** → **Sharing** → invite specific emails.

**It "went to sleep"?**
- Free apps sleep after long inactivity. Just open the link and click **"Wake up"** —
  it comes back in ~30 seconds.

**Turn on the REAL Zerodha live feed (advanced, later)**
- In `requirements.txt` remove the `#` before `kiteconnect` and re-upload it.
- In Streamlit Cloud → app **Settings** → **Secrets**, paste:
  ```
  [kite]
  api_key = "your_api_key"
  access_token = "your_daily_access_token"
  ```
- In the app sidebar pick **"Zerodha Kite (real WebSocket)"**.
  (Note: a Kite access token must be refreshed daily — that's a Zerodha rule, not the app.)

**Change the login password later**
- In Streamlit Cloud → app **Settings** → **Secrets**, edit:
  ```
  [auth]
  password = "your_new_password"
  ```
  The app reads the password from Secrets (never from the code), so nothing is exposed
  even though the repo is public. The username is `Hxrsh`.

**Reminder:** the app link is protected by the login above, so only people with the
username + password can use it. The dashboard shows public market data + a demo strategy.
Keep any broker credentials in **Secrets** (never in the files).
