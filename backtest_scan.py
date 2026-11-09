import os

import numpy as np
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["TG_TOKEN"]
CHAT = os.environ["TG_CHAT"]

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "AUDCAD"]
PAYOUT = 0.85
BREAKEVEN = 1 / (1 + PAYOUT)
K = 4
LOOKBACK = 600
TOUCH = 0.3
THRESHOLDS = [25, 40, 55, 65, 75]


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


def pattern_dir(o, h, l, c, s):
    """Return (name, +1 buy / -1 sell) for candle s, or None."""
    body = abs(c[s] - o[s])
    rng = h[s] - l[s]
    if rng <= 0:
        return None
    hi, lo = max(o[s], c[s]), min(o[s], c[s])
    up_w, lo_w = h[s] - hi, lo - l[s]
    if lo_w > 2 * body and lo_w > 0.6 * rng:
        return "pin", 1
    if up_w > 2 * body and up_w > 0.6 * rng:
        return "pin", -1
    pb_hi, pb_lo = max(o[s - 1], c[s - 1]), min(o[s - 1], c[s - 1])
    if hi >= pb_hi and lo <= pb_lo and body > abs(c[s - 1] - o[s - 1]):
        if c[s] > o[s] and c[s - 1] < o[s - 1]:
            return "engulf", 1
        if c[s] < o[s] and c[s - 1] > o[s - 1]:
            return "engulf", -1
    return None


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
    d = pd.Series(c).diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).values

    piv = []
    for j in range(K, n - K):
        if h[j] == h[j - K : j + K + 1].max():
            piv.append((j + K, h[j]))
        if l[j] == l[j - K : j + K + 1].min():
            piv.append((j + K, l[j]))
    pc_idx = np.array([p[0] for p in piv])
    pc_val = np.array([p[1] for p in piv])

    rows = []
    for i in range(200, n):
        s = i - 1
        a = atr[s]
        if not np.isfinite(a) or a <= 0 or o[i] == c[i]:
            continue
        pdir = pattern_dir(o, h, l, c, s)
        if pdir is None:
            continue
        name, side = pdir
        score = 25  # pattern
        m = (pc_idx <= s) & (pc_idx > s - LOOKBACK)
        groups = [g for g in cluster(pc_val[m], 1.0 * a) if g[1] >= 2]
        at_level = 0
        for lv, cnt in groups:
            if side == 1 and abs(l[s] - lv) <= TOUCH * a and lv < c[s]:
                at_level = max(at_level, cnt)
            if side == -1 and abs(h[s] - lv) <= TOUCH * a and lv > c[s]:
                at_level = max(at_level, cnt)
        if at_level:
            score += 25 + (5 if at_level >= 4 else 0)
        if (e20[s] > e50[s]) == (side == 1):
            score += 20
        if np.isfinite(rsi[s]) and ((side == 1 and rsi[s] < 35) or (side == -1 and rsi[s] > 65)):
            score += 15
        rows.append(
            {
                "pair": pair,
                "time": df.index[i],
                "pattern": name,
                "score": score,
                "win": (c[i] > o[i]) == (side == 1),
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
    L = ["BACKTEST SCANNER SCORE (60 maalmood, 15m)"]
    L.append("Score: pattern(25) + level S/R(25-30) + trend(20) + RSI(15)")
    L.append(f"Loo baahan yahay: {100 * BREAKEVEN:.1f}%+ (payout {int(PAYOUT * 100)}%)")
    L.append("")
    L.append(fmt("Dhammaan pattern", t["win"]))
    L.append("")
    L.append("Score ugu yaraan:")
    best = None
    for th in THRESHOLDS:
        g = t[t["score"] >= th]
        L.append("  " + fmt(f">={th}", g["win"]))
        if len(g) >= 300:
            n, p, ci = stat(g["win"])
            if best is None or p > best[1]:
                best = (th, p, g)
    if best is not None:
        L.append(halves(best[2]))
    L.append("")
    L.append("Pattern:")
    for name, g in t.groupby("pattern"):
        L.append("  " + fmt(name, g["win"]))
    L.append("")
    if best is None:
        verdict = "Xog ku filan ma jirto."
    else:
        th, p, g = best
        n, pp, ci = stat(g["win"])
        if pp - ci > BREAKEVEN:
            verdict = f"Score>={th} wuxuu ka sarreeyaa {100 * BREAKEVEN:.0f}%. Ku xaqiiji xog cusub + demo."
        elif pp + ci < BREAKEVEN:
            verdict = "Dhammaan score-yada way ka hooseeyaan 54%: lacag ayay kaa lumin lahaayeen."
        else:
            verdict = "Score sare ma bixiyo edge la hubo (random)."
    L.append("GO'AAN: " + verdict)
    L.append("Fiiro: 5 score la tijaabiyay = fursad nasiib ah. Ku xaqiiji xog cusub.")
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
        send("Backtest scan: xog ma helin.")
        return
    t = pd.concat(parts)
    rep = build_report(t)
    print(rep)
    send(rep)


if __name__ == "__main__":
    main()
