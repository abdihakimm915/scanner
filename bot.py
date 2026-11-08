import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

TOKEN = os.environ["TG_TOKEN"]
OWNER = str(os.environ["TG_CHAT"])
API = f"https://api.telegram.org/bot{TOKEN}"
RUN_SECONDS = 57 * 60
STATE_FILE = "stats.json"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "AUDCAD", "EURJPY", "GBPJPY"]
TFS = ["1m", "5m", "15m"]

WATCH = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "AUDCAD"]
SIGNAL_TF = "5m"
TF_SEC = {"1m": 60, "5m": 300, "15m": 900}
EAT = timezone(timedelta(hours=3))

STATE = {}
CHECKED = {}


def call(method, **data):
    try:
        r = requests.post(f"{API}/{method}", json=data, timeout=40)
        return r.json()
    except Exception as e:
        print("api error:", e)
        return {}


def send(text):
    call("sendMessage", chat_id=OWNER, text=text)


def kb(rows):
    return {"inline_keyboard": rows}


def btn(text, data):
    return {"text": text, "callback_data": data}


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
    except Exception:
        s = {}
    for k in ("win", "lose", "draw"):
        s.setdefault(k, 0)
    s.setdefault("pending", [])
    s.setdefault("seen", {})
    return s


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(STATE, f)
    except Exception as e:
        print("save error:", e)


def stats_text():
    w, l, d = STATE["win"], STATE["lose"], STATE["draw"]
    t = w + l
    pct = f"{100 * w / t:.0f}%" if t else "-"
    return (
        "Natiijo (qiyaas, xogta Yahoo)\n"
        f"WIN: {w}\nLOSE: {l}\nDRAW: {d}\n"
        f"Guusha: {pct}\n"
        f"Sugaya natiijo: {len(STATE['pending'])}\n\n"
        "Kuwani ma aha trade-yadaada dhabta ah."
    )


def get_df(pair, tf):
    df = yf.download(pair + "=X", period="5d", interval=tf, progress=False)
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


def market_open():
    n = datetime.now(timezone.utc)
    wd = n.weekday()
    if wd == 5:
        return False
    if wd == 6 and n.hour < 21:
        return False
    if wd == 4 and n.hour >= 22:
        return False
    return True


def home_menu():
    return "Dooro:", kb([[btn("Forex", "m:forex"), btn("OTC", "m:otc")], [btn("Natiijo", "m:stats")]])


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
    df = get_df(pair, tf)
    if df is None or len(df) < 60:
        return "Xog ma helin. Isku day mar kale."
    df["e20"] = df["Close"].ewm(span=20).mean()
    df["e50"] = df["Close"].ewm(span=50).mean()

    cur = df.iloc[-1]
    closed = df.iloc[-2]
    mother = df.iloc[-3]
    trend = "UP" if closed["e20"] > closed["e50"] else "DOWN"
    inside = closed["High"] < mother["High"] and closed["Low"] > mother["Low"]

    now = pd.Timestamp.now(tz="UTC")
    age_min = (now - df.index[-1]).total_seconds() / 60

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
    lines.append(f"Waqtiga (EAT): {hhmm(now)}")
    lines.append("Ma aha talo ganacsi. Ku tijaabi demo.")
    return "\n".join(lines)


def signal_scan():
    tfs = TF_SEC[SIGNAL_TF]
    now = pd.Timestamp.now(tz="UTC")
    bucket = int(now.timestamp() // tfs)
    for pair in WATCH:
        if CHECKED.get(pair) == bucket:
            continue
        try:
            df = get_df(pair, SIGNAL_TF)
            if df is None or len(df) < 60:
                continue
            cur_ts = df.index[-1]
            age = (now - cur_ts).total_seconds()
            if age < 0 or age >= tfs:
                continue
            CHECKED[pair] = bucket

            closed, mother = df.iloc[-2], df.iloc[-3]
            if not (closed["High"] < mother["High"] and closed["Low"] > mother["Low"]):
                continue
            key = cur_ts.isoformat()
            if STATE["seen"].get(pair) == key:
                continue

            e20 = df["Close"].ewm(span=20).mean().iloc[-2]
            e50 = df["Close"].ewm(span=50).mean().iloc[-2]
            side = "BUY" if e20 > e50 else "SELL"
            name = f"{pair[:3]}/{pair[3:]}"
            expiry = cur_ts + timedelta(seconds=tfs)

            send(
                f"SIGNAL | {name} {SIGNAL_TF}\n"
                f"Inside bar wuu xidhmay.\n"
                f"Jihada (trend): {side}\n"
                f"GAL: {hhmm(cur_ts)} (EAT), furitaanka candle-ka cusub\n"
                f"Expiry: {hhmm(expiry)} (EAT)\n"
                f"Dib u dhac: {int(age)}s. Haddii aad aragto 60s ka dib, ha gelin.\n"
                f"Natiijada waan kuu soo diri doonaa. Ku tijaabi demo."
            )
            STATE["seen"][pair] = key
            STATE["pending"].append({"pair": pair, "tf": SIGNAL_TF, "ts": key, "side": side})
            save_state()
        except Exception as e:
            print("scan error", pair, e)


def resolve_pending():
    if not STATE["pending"]:
        return
    now = pd.Timestamp.now(tz="UTC")
    keep = []
    changed = False
    for p in STATE["pending"]:
        ts = pd.Timestamp(p["ts"])
        end_ts = ts + pd.Timedelta(seconds=TF_SEC[p["tf"]])
        if now < end_ts + pd.Timedelta(seconds=20):
            keep.append(p)
            continue
        try:
            df = get_df(p["pair"], p["tf"])
        except Exception:
            df = None
        if df is None or ts not in df.index:
            if now < end_ts + pd.Timedelta(minutes=30):
                keep.append(p)
            else:
                changed = True
            continue
        if ts == df.index[-1] and now < end_ts + pd.Timedelta(seconds=90):
            keep.append(p)
            continue
        row = df.loc[ts]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        o, c = float(row["Open"]), float(row["Close"])
        if c == o:
            res, icon = "draw", "DRAW"
        elif (c > o) == (p["side"] == "BUY"):
            res, icon = "win", "WIN ✅"
        else:
            res, icon = "lose", "LOSE ❌"
        STATE[res] += 1
        changed = True
        w, l = STATE["win"], STATE["lose"]
        pct = f"{100 * w / (w + l):.0f}%" if (w + l) else "-"
        name = f"{p['pair'][:3]}/{p['pair'][3:]}"
        send(
            f"NATIIJO | {name} {p['tf']}\n"
            f"{p['side']}: {icon}\n"
            f"{hhmm(ts)} -> {hhmm(end_ts)} (EAT)\n"
            f"Open {o:.5f} -> Close {c:.5f}\n"
            f"Wadar: {w} WIN / {l} LOSE ({pct})"
        )
    STATE["pending"] = keep
    if changed:
        save_state()


def tick():
    resolve_pending()
    if market_open():
        sec = time.time() % TF_SEC[SIGNAL_TF]
        if 8 <= sec <= 150:
            signal_scan()


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
            elif rest == "stats":
                edit(chat, mid, stats_text(), kb([[btn("Back", "m:home")]]))
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
    STATE.update(load_state())
    offset = None
    last_tick = 0
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
        if time.time() - last_tick >= 25:
            last_tick = time.time()
            try:
                tick()
            except Exception as e:
                print("tick error:", e)
    if offset:
        call("getUpdates", offset=offset, timeout=0)
    save_state()


if __name__ == "__main__":
    main()
