import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["TG_TOKEN"]
OWNER = str(os.environ["TG_CHAT"])
API = f"https://api.telegram.org/bot{TOKEN}"
RUN_SECONDS = 57 * 60

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "AUDCAD", "EURJPY", "GBPJPY"]
TFS = ["1m", "5m", "15m"]


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


def home_menu():
    return "Dooro suuqa:", kb([[btn("Forex", "m:forex"), btn("OTC", "m:otc")]])


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


def analyze(pair, tf):
    df = yf.download(pair + "=X", period="5d", interval=tf, progress=False)
    if df is None or len(df) < 60:
        return "Xog ma helin. Isku day mar kale."
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    df["e20"] = df["Close"].ewm(span=20).mean()
    df["e50"] = df["Close"].ewm(span=50).mean()

    cur = df.iloc[-1]
    closed = df.iloc[-2]
    mother = df.iloc[-3]

    trend = "UP" if closed["e20"] > closed["e50"] else "DOWN"
    inside = closed["High"] < mother["High"] and closed["Low"] > mother["Low"]

    ts = df.index[-1]
    now = pd.Timestamp.now(tz="UTC")
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    age_min = (now - ts).total_seconds() / 60

    name = f"{pair[:3]}/{pair[3:]}"
    lines = [f"{name} {tf}", f"Qiimaha: {cur['Close']:.5f}", f"Trend (EMA20/50): {trend}"]
    if inside:
        side = "BUY" if trend == "UP" else "SELL"
        lines += [
            "Candle xidhan: INSIDE BAR",
            f"Buy haddii uu xidho ka sarreeya: {mother['High']:.5f}",
            f"Sell haddii uu xidho ka hooseeya: {mother['Low']:.5f}",
            f"Dhinaca la socda trend-ka: {side}",
        ]
    else:
        lines.append("Candle xidhan: inside bar ma jiro")
    if age_min > 30:
        lines.append(f"Suuqa wuu xiran yahay, xogtu waa {int(age_min // 60)} saac ka hor.")
    eat = (datetime.utcnow() + timedelta(hours=3)).strftime("%H:%M")
    lines.append(f"Waqtiga (EAT): {eat}")
    lines.append("Ma aha talo ganacsi. Ku tijaabi demo.")
    return "\n".join(lines)


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
            text = analyze(pair, tf)
            edit(chat, mid, text, kb([[btn("Refresh", q["data"]), btn("Back", f"p:{pair}")]]))


def main():
    offset = None
    end = time.time() + RUN_SECONDS
    while time.time() < end:
        params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if offset:
            params["offset"] = offset
        res = call("getUpdates", **params)
        if not res:
            time.sleep(3)
            continue
        for u in res.get("result", []):
            offset = u["update_id"] + 1
            try:
                handle(u)
            except Exception as e:
                print("handle error:", e)
    if offset:
        call("getUpdates", offset=offset, timeout=0)


if __name__ == "__main__":
    main()
