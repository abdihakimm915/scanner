import os

import numpy as np
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["TG_TOKEN"]
CHAT = os.environ["TG_CHAT"]

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "AUDCAD"]
PAYOUT = 0.85
BREAKEVEN = 1 / (1 + PAYOUT)  # ~54.1%
K = 4  # swing pivot width
LOOKBACK = 600
TOUCH = 0.3  # touch distance in ATR


def get_df(pair):
    df = yf.download(pair + "=X", period="60d", interval="15m", progress=False)
    if df is None or len(df) == 0:
        return None
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    if len(df) == 0:
        return None
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df


def cluster(levels, tol):
    groups = []
    for x in sorted(levels):
        if groups and x - groups[-1][0] <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [(float(np.mean(g)), len(g)) for g in groups]


def trades_for(pair, df=None):
    if df is None:
        df = get_df(pair)
    if df is None or len(df) < 400:
        return None
    o, h, l, c = (df[x].values for x in ("Open", "High", "Low", "Close"))
    n = len(df)
    pc = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(14).mean().values
    e20 = pd.Series(c).ewm(span=20).mean().values
    e50 = pd.Series(c).ewm(span=50).mean().values

    # pivots: pivot at j is only known at bar j+K (no look-ahead)
    piv = []  # (confirm_idx, price)
    for j in range(K, n - K):
        if h[j] == h[j - K : j + K + 1].max():
            piv.append((j + K, h[j]))
        if l[j] == l[j - K : j + K + 1].min():
            piv.append((j + K, l[j]))
    pc_idx = np.array([p[0] for p in piv])
    pc_val = np.array([p[1] for p in piv])

    rows = []
    for i in range(200, n):
        s = i - 1  # last closed candle before entry candle i
        a = atr[s]
        if not np.isfinite(a) or a <= 0:
            continue
        m = (pc_idx <= s) & (pc_idx > s - LOOKBACK)
        groups = [g for g in cluster(pc_val[m], 1.0 * a) if g[1] >= 2]
        if not groups:
            continue
        buy = sell = None
        for lv, cnt in groups:
            if abs(l[s] - lv) <= TOUCH * a and c[s] > lv and lv < c[s]:
                if buy is None or cnt > buy:
                    buy = cnt
            if abs(h[s] - lv) <= TOUCH * a and c[s] < lv and lv > c[s]:
                if sell is None or cnt > sell:
                    sell = cnt
        if (buy is None) == (sell is None):
            continue  # nothing, or ambiguous
        if o[i] == c[i]:
            continue
        side_up = buy is not None
        up = c[i] > o[i]
        trend_up = e20[s] > e50[s]
        rows.append(
            {
                "pair": pair,
                "time": df.index[i],
                "win": up == side_up,
                "touches": buy if side_up else sell,
                "with_trend": side_up == trend_up,
            }
        )
    return pd.DataFrame(rows)


def stat(w):
    n = len(w)
    if n == 0:
        return 0, 0.0, 0.0
    p = float(w.mean())
    return n, p, 1.96 * np.sqrt(p * (1 - p) / n)


def fmt(name, w):
    n, p, ci = stat(w)
    if n == 0:
        return f"{name}: n=0"
    return f"{name}: {100 * p:.1f}% (+-{100 * ci:.1f}) n={n}"


def halves(t):
    t = t.sort_values("time")
    mid = len(t) // 2
    a, b = t["win"].iloc[:mid], t["win"].iloc[mid:]
    pa = 100 * a.mean() if len(a) else 0
    pb = 100 * b.mean() if len(b) else 0
    return f"   1st half {pa:.1f}% | 2nd half {pb:.1f}%"


def build_report(t):
    L = ["BACKTEST S/R (60 maalmood, 15m, Yahoo)"]
    L.append("Qodobka: candle taabtay support/resistance -> trade-ka candle xiga")
    L.append("(support = BUY, resistance = SELL, expiry = 1 candle)")
    L.append(f"Payout {int(PAYOUT * 100)}% => loo baahan yahay: {100 * BREAKEVEN:.1f}%+")
    L.append("")
    L.append(fmt("Dhammaan (laabasho)", t["win"]))
    L.append(halves(t))
    L.append(fmt("Taa ka soo horjeeda (jebin)", ~t["win"]))
    L.append("")
    L.append("Lacag kasta:")
    for pair, g in t.groupby("pair"):
        L.append("  " + fmt(pair, g["win"]))
    L.append("")
    L.append("Shuruudo (ma aha mid la xaqiijiyay):")
    s3 = t[t["touches"] >= 4]
    L.append(fmt("Level xoog badan (4+ taabasho)", s3["win"]))
    wt = t[t["with_trend"]]
    L.append(fmt("Jihada trend-ka kaliya", wt["win"]))
    L.append(halves(wt) if len(wt) else "")
    n, p, ci = stat(t["win"])
    L.append("")
    if n == 0:
        verdict = "Xog ma jirto."
    elif p - ci > BREAKEVEN:
        verdict = "Waxaa jira calaamad faa'iido, laakiin ku xaqiiji xog cusub + demo."
    elif p + ci < BREAKEVEN:
        verdict = f"Ka hooseeya {100 * BREAKEVEN:.0f}%: lacag ayuu kaa lumin lahaa."
    else:
        verdict = "Wax edge ah lama arkin (natiijadu waxay u eg tahay random)."
    L.append("GO'AAN: " + verdict)
    L.append("Fiiro: entry = Open candle-ka, run ahaan waa ka xun yahay (spread + dib u dhac).")
    return "\n".join(L)


def send(text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": text[:4000]},
        timeout=30,
    )


def main():
    parts = []
    for p in PAIRS:
        try:
            t = trades_for(p)
            if t is not None and len(t):
                parts.append(t)
        except Exception as e:
            print(p, "error:", e)
    if not parts:
        send("Backtest S/R: xog ma helin.")
        return
    t = pd.concat(parts)
    rep = build_report(t)
    print(rep)
    send(rep)


if __name__ == "__main__":
    main()
