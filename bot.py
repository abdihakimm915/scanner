import json
import os
import subprocess
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
LAST = {}
JOURNAL = "journal.json"
BREAKEVEN = 1 / 1.68
API_KEY = os.environ.get("API_KEY", "")
MODELS = ["gemini-2.5-flash", "gemini-flash-latest"]
PROMPT = (
    "Waxaad tahay kaaliye akhriya chart-ka forex. Sawirkan waa screenshot chart candlestick ah. "
    "Ku jawaab AF SOOMAALI, gaaban (ugu badnaan 10 sadar). Qaab: "
    "1) Trend: UP/DOWN/Sideways. "
    "2) Heerarka S/R ee muuqda (sheeg qiimaha haddii uu muuqdo, haddii kale 'sare'/'hoose'). "
    "3) Candle-ka ugu dambeeya iyo pattern-ka (pin bar, engulfing, inside bar) haddii uu jiro. "
    "4) Go'aan: UP (BUY), DOWN (SELL) ama No clear read, iyo sababta gaaban. "
    "Haddii chart-ku aanu cadayn, ama qiimaha/pair-ka aan la arki karin, sheeg. "
    "Ha ballanqaadin guul. Ku dar sadarka ugu dambeeya: 'Ma aha saadaal. Demo kaliya.'"
)


def read_photo(file_id):
    f = call("getFile", file_id=file_id)
    path = f.get("result", {}).get("file_path")
    if not path:
        return "Sawirka ma helin. Isku day mar kale."
    img = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=40).content
    import base64

    b64 = base64.b64encode(img).decode()
    body = {
        "contents": [
            {"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": "image/jpeg", "data": b64}}]}
        ]
    }
    last = ""
    for m in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                headers={"x-goog-api-key": API_KEY},
                json=body,
                timeout=90,
            )
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts).strip() or "Jawaab madhan. Isku day mar kale."
            last = f"{r.status_code}: {r.text[:200]}"
            print("gemini error", m, last)
        except Exception as e:
            last = str(e)
            print("gemini exception", m, last)
    return "AI ma shaqeynin. Hubi API_KEY ama isku day mar kale.\n(" + last[:120] + ")"


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
    LAST[(pair, tf)] = (df, res, sup)

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


def make_chart(pair, tf, df, res, sup):
    try:
        import matplotlib
    except ImportError:
        import subprocess
        import sys

        subprocess.run([sys.executable, "-m", "pip", "install", "matplotlib"], check=True)
        import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = df.tail(100)
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=110)
    fig.patch.set_facecolor("#0e1117")
    ax.set_facecolor("#0e1117")
    for i, (_, r) in enumerate(d.iterrows()):
        up = r["Close"] >= r["Open"]
        col = "#26a69a" if up else "#ef5350"
        ax.vlines(i, r["Low"], r["High"], color=col, linewidth=0.8)
        lo, hi = min(r["Open"], r["Close"]), max(r["Open"], r["Close"])
        ax.add_patch(plt.Rectangle((i - 0.35, lo), 0.7, max(hi - lo, 1e-9), color=col))
    dec = 3 if "JPY" in pair else 5
    n = len(d)
    for lv, cnt in res:
        ax.hlines(lv, -1, n, color="#ef5350", linestyle="--", linewidth=1)
        ax.text(n + 0.5, lv, f"R {lv:.{dec}f} ({cnt}x)", color="#ef5350", fontsize=8, va="center")
    for lv, cnt in sup:
        ax.hlines(lv, -1, n, color="#26a69a", linestyle="--", linewidth=1)
        ax.text(n + 0.5, lv, f"S {lv:.{dec}f} ({cnt}x)", color="#26a69a", fontsize=8, va="center")
    step = max(n // 6, 1)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([hhmm(d.index[i]) for i in ticks], color="#9aa0a6", fontsize=8)
    ax.tick_params(axis="y", colors="#9aa0a6", labelsize=8)
    ax.set_xlim(-1, n + 14)
    for sp in ax.spines.values():
        sp.set_color("#2a2f3a")
    ax.set_title(f"{pair[:3]}/{pair[3:]} {tf} (EAT)", color="white", fontsize=11)
    fig.tight_layout()
    path = f"chart_{pair}_{tf}.png"
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def send_chart(chat, pair, tf):
    if (pair, tf) not in LAST:
        analyze(pair, tf)
    if (pair, tf) not in LAST:
        call("sendMessage", chat_id=chat, text="Xog ma helin. Isku day mar kale.")
        return
    df, res, sup = LAST[(pair, tf)]
    path = make_chart(pair, tf, df, res, sup)
    with open(path, "rb") as f:
        requests.post(
            f"{API}/sendPhoto",
            data={"chat_id": chat, "caption": f"{pair[:3]}/{pair[3:]} {tf}: qalab akhris, ma aha saadaal."},
            files={"photo": f},
            timeout=60,
        )


def jload():
    try:
        with open(JOURNAL) as f:
            return json.load(f)
    except Exception:
        return []


def jsave(data):
    with open(JOURNAL, "w") as f:
        json.dump(data, f)
    try:
        run = lambda *a: subprocess.run(a, check=True, capture_output=True)
        run("git", "config", "user.name", "bot")
        run("git", "config", "user.email", "bot@users.noreply.github.com")
        run("git", "add", JOURNAL)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
            run("git", "commit", "-m", "journal")
            run("git", "pull", "--rebase")
            run("git", "push")
    except Exception as e:
        print("journal save error:", e)


def journal_text():
    data = jload()
    done = [t for t in data if t.get("res") in ("W", "L")]
    pend = [t for t in data if t.get("res") is None]
    n = len(done)
    lines = ["BUUGGA DEMO", ""]
    if n == 0:
        lines.append("Wali trade dhammaaday ma jiro.")
    else:
        w = sum(1 for t in done if t["res"] == "W")
        p = w / n
        ci = 1.96 * (p * (1 - p) / n) ** 0.5
        lines.append(f"Trades: {n}  |  WIN {w}  LOSE {n - w}")
        lines.append(f"Guusha: {100 * p:.1f}% (+-{100 * ci:.1f})")
        lines.append(f"Loo baahan yahay: {100 * BREAKEVEN:.1f}%+")
        if n < 30:
            lines.append("Wali ku filan ma aha (u baahan tahay 50-100 trade).")
        elif p - ci > BREAKEVEN:
            lines.append("Waa ka sarreeyaa xadka. Weli ku sii wad demo.")
        elif p + ci < BREAKEVEN:
            lines.append("Ka hooseeya xadka: lacag ayaad lumin lahayd.")
        else:
            lines.append("Wali caddayn la'aan (random).")
    if pend:
        lines.append("")
        lines.append(f"Sugaya natiijo: {len(pend)}")
    last = data[-10:]
    if last:
        lines.append("")
        lines.append("Ugu dambeeyay:")
        for t in last:
            r = {"W": "WIN", "L": "LOSE", "X": "tirtir"}.get(t.get("res"), "...")
            side = "BUY" if t["side"] == "B" else "SELL"
            lines.append(f"#{t['id']} {t['pair'][:3]}/{t['pair'][3:]} {t['tf']} {side} {t['time']} {r}")
    lines.append("")
    lines.append("Ku tijaabi demo kaliya.")
    return "\n".join(lines)


def read_signal(pair, tf):
    if (pair, tf) not in LAST:
        analyze(pair, tf)
    if (pair, tf) not in LAST:
        return "Xog ma helin. Isku day mar kale."
    df, res, sup = LAST[(pair, tf)]
    d = df
    o, h, l, c = (d[x].values for x in ("Open", "High", "Low", "Close"))
    i = -2
    a = float(atr(d).iloc[-2])
    r = float(rsi(d["Close"]).iloc[-2])
    e20 = d["Close"].ewm(span=20).mean().iloc[-2]
    e50 = d["Close"].ewm(span=50).mean().iloc[-2]
    trend_up = e20 > e50

    body = abs(c[i] - o[i])
    rng = h[i] - l[i]
    hi, lo = max(o[i], c[i]), min(o[i], c[i])
    side, reasons, score = None, [], 0
    if rng > 0:
        up_w, lo_w = h[i] - hi, lo - l[i]
        if lo_w > 2 * body and lo_w > 0.6 * rng:
            side, score = 1, 25
            reasons.append("Pin bar hoose (wick dheer hoose)")
        elif up_w > 2 * body and up_w > 0.6 * rng:
            side, score = -1, 25
            reasons.append("Pin bar sare (wick dheer sare)")
        else:
            pb_hi, pb_lo = max(o[i - 1], c[i - 1]), min(o[i - 1], c[i - 1])
            if hi >= pb_hi and lo <= pb_lo and body > abs(c[i - 1] - o[i - 1]):
                if c[i] > o[i] and c[i - 1] < o[i - 1]:
                    side, score = 1, 25
                    reasons.append("Bullish engulfing")
                elif c[i] < o[i] and c[i - 1] > o[i - 1]:
                    side, score = -1, 25
                    reasons.append("Bearish engulfing")

    touch = None
    if sup and l[i] <= sup[0][0] + 0.3 * a and c[i] > sup[0][0]:
        touch = (1, sup[0][1], "taabtay SUPPORT")
    if res and h[i] >= res[0][0] - 0.3 * a and c[i] < res[0][0]:
        t2 = (-1, res[0][1], "taabtay RESISTANCE")
        touch = t2 if touch is None else None  # labadaba = isku dhac
    if touch:
        if side is None:
            side = touch[0]
            score += 25
            reasons.append(f"Candle {touch[2]} ({touch[1]} taabasho)")
        elif touch[0] == side:
            score += 25 + (5 if touch[1] >= 4 else 0)
            reasons.append(f"Candle {touch[2]} ({touch[1]} taabasho)")
        else:
            score -= 15
            reasons.append("S/R iyo pattern way is khilaafsan yihiin")

    if side is not None:
        if trend_up == (side == 1):
            score += 20
            reasons.append("Waafaqsan trend-ka (EMA20/50)")
        else:
            reasons.append("Ka soo horjeeda trend-ka")
        if (side == 1 and r < 35) or (side == -1 and r > 65):
            score += 15
            reasons.append(f"RSI {r:.0f} (xad dheer)")

    lines = [f"AKHRI | {pair[:3]}/{pair[3:]} {tf}"]
    if side is None or score < 45:
        lines.append("No clear read (calaamad cad ma jirto)")
        if reasons:
            lines.extend("- " + x for x in reasons)
    else:
        lines.append(f"{'UP (BUY)' if side == 1 else 'DOWN (SELL)'}  |  score {min(score, 100)}/100")
        lines.extend("- " + x for x in reasons)
    lines.append("")
    lines.append("Taariikh (13,000+ trade oo la tijaabiyay): ~50% guul, score sare ma kordhiyo.")
    lines.append(f"Loo baahan yahay si aadan lacag lumin: {100 * BREAKEVEN:.1f}%+")
    lines.append("Ma aha saadaal. Ku tijaabi demo kaliya.")
    return "\n".join(lines)


def home_menu():
    return "Dooro:", kb([[btn("Forex", "m:forex"), btn("OTC", "m:otc")], [btn("Scan dhammaan (15m)", "s:15m")], [btn("Buugga demo", "m:book")]])


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
        if "photo" in m:
            call("sendMessage", chat_id=OWNER, text="Waan akhrinayaa sawirka...")
            try:
                txt = read_photo(m["photo"][-1]["file_id"]) if API_KEY else "API_KEY lama dhigin GitHub Secrets."
            except Exception as e:
                print("photo error:", e)
                txt = "Qalad ayaa dhacay. Isku day mar kale."
            call("sendMessage", chat_id=OWNER, text=txt)
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
            if rest == "book":
                edit(chat, mid, journal_text(), kb([[btn("Back", "m:home")]]))
            elif rest == "forex":
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
            edit(
                chat,
                mid,
                text,
                kb(
                    [
                        [btn("Refresh", q["data"]), btn("Chart", f"c:{pair}:{tf}")],
                        [btn("Akhri (UP/DOWN)", f"a:{pair}:{tf}")],
                        [btn("Log BUY", f"l:{pair}:{tf}:B"), btn("Log SELL", f"l:{pair}:{tf}:S")],
                        [btn("Back", f"p:{pair}")],
                    ]
                ),
            )
        elif kind == "l":
            pair, tf, side = rest.split(":")
            data = jload()
            tid = (max([t["id"] for t in data]) + 1) if data else 1
            price = None
            if (pair, tf) in LAST:
                price = float(LAST[(pair, tf)][0]["Close"].iloc[-1])
            data.append(
                {
                    "id": tid,
                    "pair": pair,
                    "tf": tf,
                    "side": side,
                    "price": price,
                    "time": hhmm(pd.Timestamp.now(tz="UTC")),
                    "res": None,
                }
            )
            jsave(data)
            sname = "BUY" if side == "B" else "SELL"
            call(
                "sendMessage",
                chat_id=chat,
                text=f"Trade #{tid} la duubay: {pair[:3]}/{pair[3:]} {tf} {sname}.\nMarka Pocket Option demo natiijo yeesho, dooro:",
                reply_markup=kb([[btn("WIN", f"r:{tid}:W"), btn("LOSE", f"r:{tid}:L")], [btn("Tirtir", f"r:{tid}:X")]]),
            )
        elif kind == "r":
            tid, res = rest.split(":")
            data = jload()
            for t in data:
                if str(t["id"]) == tid:
                    t["res"] = res
            if res == "X":
                data = [t for t in data if str(t["id"]) != tid]
            jsave(data)
            edit(chat, mid, journal_text(), kb([[btn("Back", "m:home")]]))
        elif kind == "a":
            pair, _, tf = rest.partition(":")
            try:
                txt = read_signal(pair, tf)
            except Exception as e:
                print("read error:", e)
                txt = "Qalad ayaa dhacay. Isku day mar kale."
            call("sendMessage", chat_id=chat, text=txt)
        elif kind == "s":
            edit(chat, mid, "Waan scan-gareynayaa 8 lacagood...", kb([]))
            out = [f"SCAN {rest} (taariikh ahaan ~50% guul)", ""]
            for p in PAIRS:
                try:
                    ln = read_signal(p, rest).split("\n")[1]
                except Exception as e:
                    print("scan error:", e)
                    ln = "xog ma jirto"
                out.append(f"{p[:3]}/{p[3:]}: {ln}")
            out.append("")
            out.append("Demo kaliya. Ma aha saadaal.")
            edit(chat, mid, "\n".join(out), kb([[btn("Scan mar kale", f"s:{rest}")], [btn("Back", "m:home")]]))
        elif kind == "c":
            pair, _, tf = rest.partition(":")
            try:
                send_chart(chat, pair, tf)
            except Exception as e:
                print("chart error:", e)
                call("sendMessage", chat_id=chat, text="Chart ma samaysmin. Isku day mar kale.")


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
