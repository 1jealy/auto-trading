"""관찰용: 업비트 최근 7일 체결 기록(KRW-BTC) 받기.  python scripts/obs_fetch_ticks.py data/ticks/btc_ticks.pkl"""
import sys, time
from pathlib import Path
import pandas as pd, requests

OUT = Path(sys.argv[1]); OUT.parent.mkdir(parents=True, exist_ok=True)
s = requests.Session()
rows = []
for days_ago in range(7, 0, -1):
    cursor = None
    n0 = len(rows)
    while True:
        params = {"market": "KRW-BTC", "count": 500, "days_ago": days_ago}
        if cursor:
            params["cursor"] = cursor
        for wait in (0.5, 1, 2, 4, 8):
            r = s.get("https://api.upbit.com/v1/trades/ticks", params=params, timeout=10)
            if r.status_code == 429:
                time.sleep(wait); continue
            r.raise_for_status(); break
        page = r.json()
        if not page:
            break
        rows.extend((p["timestamp"], p["trade_price"], p["trade_volume"], p["ask_bid"], p["sequential_id"]) for p in page)
        cursor = page[-1]["sequential_id"]
        time.sleep(0.11)
        if len(page) < 500:
            break
    print(f"days_ago={days_ago}: {len(rows) - n0} ticks", flush=True)

df = pd.DataFrame(rows, columns=["ts", "price", "volume", "side", "seq"]).drop_duplicates("seq")
df["time"] = pd.to_datetime(df["ts"], unit="ms")
df = df.sort_values(["ts", "seq"]).reset_index(drop=True)
df.to_pickle(OUT)
print(len(df), df["time"].min(), df["time"].max())
