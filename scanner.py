import os
import requests
import yfinance as yf

PAIRS = ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "AUDCAD=X"]
INTERVAL = "5m"

TOKEN = os.environ["TG_TOKEN"]
CHAT = os.environ["TG_CHAT"]


def send(msg):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT, "text": msg},
        timeout=15,
    )


def scan(pair):
    df = yf.download(pair, period="2d", interval=INTERVAL, progress=False)
    if df is None or len(df) < 60:
        return None
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)

    df["ema20"] = df["Close"].ewm(span=20).mean()
    df["ema50"] = df["Close"].ewm(span=50).mean()

    inside = df.iloc[-2]
    mother = df.iloc[-3]

    is_inside = inside["High"] < mother["High"] and inside["Low"] > mother["Low"]
    if not is_inside:
        return None

    trend = "UP" if inside["ema20"] > inside["ema50"] else "DOWN"
    name = pair.replace("=X", "")
    return (
        f"INSIDE BAR | {name} {INTERVAL}\n"
        f"Trend (EMA20/50): {trend}\n"
        f"Buy haddii uu xidho ka sarreeya: {mother['High']:.5f}\n"
        f"Sell haddii uu xidho ka hooseeya: {mother['Low']:.5f}\n"
        f"Ku tijaabi demo ka hor."
    )


if __name__ == "__main__":
    send("Scanner wuu shaqeynayaa (fariin tijaabo).")
    for p in PAIRS:
        try:
            m = scan(p)
            if m:
                send(m)
        except Exception as e:
            print(p, "error:", e)
