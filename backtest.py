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


def get_df(pair):
    df = yf.download(pair + "=X", period="60d", interval="5m", progress=False)
    if df is None or len(df) == 0:
        return None
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df


def trades_for(pair):
    df = get_df(pair)
    if df is None or len(df) < 200:
        return None
    e20 = df["Close"].ewm(span=20).mean()
    e50 = df["Close"].ewm(span=50).mean()
    h, l = df["High"], df["Low"]

    # candle i-1 = inside bar, i-2 = mother, candle i = entry candle
    inside = (h.shift(1) < h.shift(2)) & (l.shift(1) > l.shift(2))
    step = df.index.to_series().diff()
    contiguous = (step == pd.Timedelta(minutes=5)) & (step.shift(1) == pd.Timedelta(minutes=5))
    ok = inside & contiguous

    out = pd.DataFrame(
        {
            "pair": pair,
            "trend_up": (e20.shift(1) > e50.shift(1)),
            "strength": ((e20 - e50).abs() / df["Close"]).shift(1),
            "ratio": ((h - l).shift(1) / (h - l).shift(2)),
            "up": df["Close"] > df["Open"],
            "flat": df["Close"] == df["Open"],
        },
        index=df.index,
    )
    out = out[ok & ~out["flat"]].dropna()
    out["win"] = out["up"] == out["trend_up"]
    return out


def stat(mask_win):
    n = len(mask_win)
    if n == 0:
        return 0, 0.0, 0.0
    p = mask_win.mean()
    ci = 1.96 * np.sqrt(p * (1 - p) / n)
    return n, p, ci


def fmt(name, w):
    n, p, ci = stat(w)
    if n == 0:
        return f"{name}: n=0"
    return f"{name}: {100 * p:.1f}% (+-{100 * ci:.1f}) n={n}"


def halves(t, w):
    order = t.index.argsort()
    mid = len(t) // 2
    a = w.iloc[order[:mid]]
    b = w.iloc[order[mid:]]
    pa = 100 * a.mean() if len(a) else 0
    pb = 100 * b.mean() if len(b) else 0
    return f"   1st half {pa:.1f}% | 2nd half {pb:.1f}%"


def build_report(t):
    lines = []
    lines.append("BACKTEST (60 maalmood, 5m, Yahoo)")
    lines.append(f"Payout {int(PAYOUT * 100)}% => guusha loo baahan yahay: {100 * BREAKEVEN:.1f}%+")
    lines.append("")
    w = t["win"]
    lines.append(fmt("Dhammaan (jihada trend)", w))
    lines.append(halves(t, w))
    lines.append(fmt("Ka soo horjeedka trend", ~w))
    lines.append("")

    lines.append("Lacag kasta:")
    for pair, g in t.groupby("pair"):
        lines.append("  " + fmt(pair, g["win"]))

    lines.append("")
    lines.append("Shuruudo (waa in la dhawro, ma aha mid la xaqiijiyay):")
    thr = t["strength"].quantile(2 / 3)
    strong = t[t["strength"] >= thr]
    lines.append(fmt("Trend xoog badan (1/3 ugu sarreysa)", strong["win"]))
    lines.append(halves(strong, strong["win"]))
    small = t[t["ratio"] < 0.5]
    lines.append(fmt("Inside bar yar (<50% mother)", small["win"]))
    lines.append(halves(small, small["win"]))

    lines.append("")
    eat = t.index.tz_convert("Africa/Nairobi").hour
    hh = t.groupby(eat)["win"].agg(["mean", "count"])
    hh = hh[hh["count"] >= 40].sort_values("mean", ascending=False)
    lines.append("Saacadaha EAT (n>=40):")
    for h_, r in hh.head(3).iterrows():
        lines.append(f"  {h_:02d}:00 -> {100 * r['mean']:.1f}% n={int(r['count'])}")
    for h_, r in hh.tail(2).iterrows():
        lines.append(f"  {h_:02d}:00 -> {100 * r['mean']:.1f}% n={int(r['count'])}")

    n, p, ci = stat(w)
    lines.append("")
    if p - ci > BREAKEVEN:
        verdict = "Waxaa jira calaamad faa'iido, laakiin ku xaqiiji xog cusub + demo."
    elif p + ci < BREAKEVEN:
        verdict = f"Xaddiga guud wuxuu ka hooseeyaa {100 * BREAKEVEN:.0f}%: strategy-gan lacag ayuu kaa lumin lahaa."
    else:
        verdict = "Wax edge ah lama arkin (natiijadu waxay u eg tahay random)."
    lines.append("GO'AAN: " + verdict)
    lines.append("Fiiro: entry-ga backtest wuxuu isticmaalaa Open-ka candle-ka, run ahaan waa ka xun yahay (dib u dhac + spread).")
    return "\n".join(lines)


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
        send("Backtest: xog ma helin.")
        return
    t = pd.concat(parts).sort_index()
    rep = build_report(t)
    print(rep)
    send(rep)


if __name__ == "__main__":
    main()
