import os
import time
from datetime import timedelta, timezone

import numpy as np
import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["TG_TOKEN"]
OWNER = str(os.environ["TG_CHAT"])
API = f"https://api.telegram.org/bot{TOKEN}"
RUN_SECONDS = 57 * 60

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "AUDCAD", "EURJPY", "GBPJPY"]
TFS = ["5m", "15m", "1h"]
PERIOD = {"5m": "5d", "15m": "10d", "1h": "60d"}
EAT = timezone(timedelta(hours=3))


def call(method, **data):
    try:
        r = requests.post(f"{API}/{method}", json=data, timeout=40)
        return r.json()
    except Exception as e:
        print("api error:", e)
        return {}


def kb(rows):
    return {"inline_keyboard": rows}


def btn(text, data):
    return {"text": text, "callback_data": data}


def get_df(pair, tf):
    df = yf.download(pair + "=X", period=PERIOD.get(tf, "30d"), interval=tf, progress=False)
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


def hhmm(ts):
    return ts.tz_convert(EAT).strftime("%H:%M")


def atr(df, n=14):
    pc = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def trend_of(df):
    e20 = df["Close"].ewm(span=20).mean().iloc[-2]
    e50 = df["Close"].ewm(span=50).mean().iloc[-2]
    gap = abs(e20 - e50) / df["Close"].iloc[-2]
    word = "UP" if e20 > e50 else "DOWN"
    strength = "xoog" if gap > 0.0008 else "daciif"
    return word, strength


def swing_levels(df, k=4, lookback=600):
    d = df.tail(lookback)
    h, l = d["High"].values, d["Low"].values
    lv = []
    for i in range(k, len(d) - k):
        if h[i] == h[i - k : i + k + 1].max():
            lv.append(h[i])
        if l[i] == l[i - k : i + k + 1].min():
            lv.append(l[i])
    return sorted(lv)


def cluster(levels, tol):
    groups = []
    for x in levels:
        if groups and x - groups[-1][0] <= tol:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [(float(np.mean(g)), len(g)) for g in groups]


def candle_note(df):
    c, p = df.iloc[-2], df.iloc[-3]
    body = abs(c["Close"] - c["Open"])
    rng = c["High"] - c["Low"]
    notes = []
    if c["High"] < p["High"] and c["Low"] > p["Low"]:
        notes.append("Inside bar")
    p_body_hi, p_body_lo = max(p["Open"], p["Close"]), min(p["Open"], p["Close"])
    c_body_hi, c_body_lo = max(c["Open"], c["Close"]), min(c["Open"], c["Close"])
    if c_body_hi >= p_body_hi and c_body_lo <= p_body_lo and body > abs(p["Close"] - p["Open"]):
        if c["Close"] > c["Open"] and p["Close"] < p["Open"]:
            notes.append("Bullish engulfing")
        elif c["Close"] < c["Open"] and p["Close"] > p["Open"]:
            notes.append("Bearish engulfing")
    if rng > 0:
        up_w = c["High"] - c_body_hi
        lo_w = c_body_lo - c["Low"]
        if lo_w > 2 * body and lo_w > 0.6 * rng:
            notes.append("Pin bar hoose (wick dheer hoose)")
        if up_w > 2 * body and up_w > 0.6 * rng:
            notes.append("Pin bar sare (wick dheer sare)")
    return ", ".join(notes) if notes else "caadi ah"


def analyze(pair, tf):
    df = get_df(pair, tf)
    if df is None or len(df) < 80:
        return "Xog ma helin. Isku day mar kale."

    jpy = "JPY" in pair
    pip = 0.01 if jpy else 0.0001
    dec = 3 if jpy else 5
    price = float(df["Close"].iloc[-1])
    a = float(atr(df).iloc[-2])
    r = float(rsi(df["Close"]).iloc[-2])

    t_word, t_str = trend_of(df)
    h_word, h_line = None, None
    if tf != "1h":
        htf = get_df(pair, "1h")
        if htf is not None and len(htf) > 60:
            h_word, h_str = trend_of(htf)
            h_line = f"Trend 1h: {h_word} ({h_str})"
        else:
            h_line = "Trend 1h: xog ma jirto"

    groups = cluster(swing_levels(df), 1.0 * a)
    strong = [g for g in groups if g[1] >= 2]
    if len(strong) >= 2:
        groups = strong
    res = sorted([g for g in groups if g[0] > price], key=lambda g: g[0])[:2]
    sup = sorted([g for g in groups if g[0] < price], key=lambda g: -g[0])[:2]

    def pips(x):
        return abs(x - price) / pip

    lines = [
        f"{pair[:3]}/{pair[3:]} {tf}",
        f"Qiimaha: {price:.{dec}f}",
        f"Trend {tf}: {t_word} ({t_str})",
        *([h_line] if h_line else []),
        f"RSI(14): {r:.0f}" + (" (sarreeya)" if r >= 70 else " (hooseeya)" if r <= 30 else ""),
        f"ATR(14): {a / pip:.1f} pips",
        "",
        "Resistance (sare):",
    ]
    if res:
        for lv, n in res:
            lines.append(f"  {lv:.{dec}f}  (+{pips(lv):.0f} pips, {n} taabasho)")
    else:
        lines.append("  ma jiro (qiimaha heer sare cusub)")
    lines.append("Support (hoose):")
    if sup:
        for lv, n in sup:
            lines.append(f"  {lv:.{dec}f}  (-{pips(lv):.0f} pips, {n} taabasho)")
    else:
        lines.append("  ma jiro (qiimaha heer hoose cusub)")

    lines.append("")
    lines.append(f"Candle xidhan: {candle_note(df)}")

    # Xaaladda guud (faahfaahin, ma aha talo)
    near = []
    if res and (res[0][0] - price) < 0.5 * a:
        near.append("dhow RESISTANCE")
    if sup and (price - sup[0][0]) < 0.5 * a:
        near.append("dhow SUPPORT")
    if near:
        lines.append("Goobta: " + " & ".join(near) + " (< 0.5 ATR)")
    else:
        lines.append("Goobta: dhex (meel gaar ah ma dhowa)")
    if h_word and h_word == t_word:
        lines.append(f"Trend {tf} iyo 1h waa isku jiho ({t_word}).")
    elif h_word:
        lines.append(f"Trend {tf} iyo 1h way is khilaafsan yihiin: taxaddar.")

    now = pd.Timestamp.now(tz="UTC")
    age_min = (now - df.index[-1]).total_seconds() / 60
    if age_min > (90 if tf == "1h" else 30):
        lines.append(f"Suuqa wuu xiran yahay, xogtu waa {int(age_min // 60)} saac ka hor.")
    lines.append(f"Waqtiga (EAT): {hhmm(now)}")
    lines.append("")
    lines.append("Tani waa qalab lagu akhriyo suuqa, ma aha saadaal. Ku tijaabi demo.")
    return "\n".join(lines)


def home_menu():
    return "Dooro:", kb([[btn("Forex", "m:forex"), btn("OTC", "m:otc")]])


def pair_menu():
    rows, row = [], []
    for p in PAIRS:
        row.append(btn(f"{p[:3]}/{p[3:]}", f"p:{p}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("Back", "m:home")])
    return "Dooro lacagta:", kb(rows)


def tf_menu(pair):
    row = [btn(t, f"t:{pair}:{t}") for t in TFS]
    return f"{pair[:3]}/{pair[3:]}: dooro timeframe", kb([row, [btn("Back", "m:forex")]])


def otc_text():
    return (
        "OTC waa qiimo uu broker-ku laftiisu sameeyo (maalmaha suuqa xiran). "
        "Xogtiisa lama heli karo, sidaa darteed bot-kan kuma shaqeeyo OTC. "
        "Isticmaal Forex (Isniin-Jimce).",
        kb([[btn("Back", "m:home")]]),
    )


def edit(chat, mid, text, markup):
    call("editMessageText", chat_id=chat, message_id=mid, text=text, reply_markup=markup)


def handle(u):
    if "message" in u:
        m = u["message"]
        if str(m["chat"]["id"]) != OWNER:
            return
        text, markup = home_menu()
        call("sendMessage", chat_id=OWNER, text=text, reply_markup=markup)
        return

    if "callback_query" in u:
        q = u["callback_query"]
        msg = q["message"]
        if str(msg["chat"]["id"]) != OWNER:
            return
        call("answerCallbackQuery", callback_query_id=q["id"])
        chat, mid = msg["chat"]["id"], msg["message_id"]
        kind, _, rest = q["data"].partition(":")

        if kind == "m":
            if rest == "forex":
                edit(chat, mid, *pair_menu())
            elif rest == "otc":
                edit(chat, mid, *otc_text())
            else:
                edit(chat, mid, *home_menu())
        elif kind == "p":
            edit(chat, mid, *tf_menu(rest))
        elif kind == "t":
            pair, _, tf = rest.partition(":")
            edit(chat, mid, "Waan eegayaa...", kb([]))
            try:
                text = analyze(pair, tf)
            except Exception as e:
                print("analyze error:", e)
                text = "Qalad ayaa dhacay. Isku day mar kale."
            edit(chat, mid, text, kb([[btn("Refresh", q["data"]), btn("Back", f"p:{pair}")]]))


def main():
    offset = None
    end = time.time() + RUN_SECONDS
    while time.time() < end:
        params = {"timeout": 20, "allowed_updates": ["message", "callback_query"]}
        if offset:
            params["offset"] = offset
        res = call("getUpdates", **params)
        if not res:
            time.sleep(3)
        for u in (res or {}).get("result", []):
            offset = u["update_id"] + 1
            try:
                handle(u)
            except Exception as e:
                print("handle error:", e)
    if offset:
        call("getUpdates", offset=offset, timeout=0)


if __name__ == "__main__":
    main()
