"""모의 투자 기록 저장소 (SQLite). 엔진이 쓰고, 화면 서버가 읽는다.

시각은 모두 유닉스 초(UTC). 날짜 묶음은 한국 시간 기준.
저장 위치는 data/paper/ (Git 제외). 환경 변수 PAPER_DATA_DIR 로 바꿀 수 있다(테스트·시험용).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.environ.get("PAPER_DATA_DIR") or ROOT / "data" / "paper")
DB_PATH = DATA_DIR / "paper.db"
LIVE_PATH = DATA_DIR / "live.json"
SLOW_PATH = DATA_DIR / "slow.json"
KST = 9 * 3600
OFFICIAL_SOURCE = "okx_swap"  # 2026-11-15 시작 전 바이낸스 선물에서 변경 (docs/experiments.md)
EVENT_GAP = 60.0  # 신호가 이 초 안에 이어지면 한 사건으로 묶는다(동시에 움직인 코인을 따로 세지 않게)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY, key TEXT, coin TEXT, source TEXT, signal_t REAL, entry_t REAL, exit_signal_t REAL,
  exit_t REAL, amount REAL, entry_price REAL, exit_price REAL, reason TEXT, gross_pct REAL, net_pct REAL,
  net_krw REAL, alt_net_pct REAL, move REAL, lag REAL, detail TEXT);
CREATE INDEX IF NOT EXISTS trades_key ON trades(key, source, exit_t);
CREATE TABLE IF NOT EXISTS skips (id INTEGER PRIMARY KEY, key TEXT, coin TEXT, source TEXT, t REAL, why TEXT,
  move REAL, lag REAL);
CREATE TABLE IF NOT EXISTS radar (id INTEGER PRIMARY KEY, coin TEXT, source TEXT, t0 REAL, move REAL, lag REAL,
  ref_ask REAL, ask0 REAL, bid0 REAL, spread REAL, points TEXT);
CREATE INDEX IF NOT EXISTS radar_src ON radar(source, t0);
CREATE TABLE IF NOT EXISTS costs (id INTEGER PRIMARY KEY, t REAL, coin TEXT, exchange TEXT, spread REAL,
  rt1 REAL, rt5 REAL, rt10 REAL);
CREATE INDEX IF NOT EXISTS costs_t ON costs(exchange, coin, t);
CREATE TABLE IF NOT EXISTS uptime (id INTEGER PRIMARY KEY, start REAL, last REAL, source TEXT);
CREATE TABLE IF NOT EXISTS refills (id INTEGER PRIMARY KEY, key TEXT, source TEXT, t REAL, amount REAL);
CREATE TABLE IF NOT EXISTS funding (t REAL PRIMARY KEY, rate REAL, source TEXT);
CREATE TABLE IF NOT EXISTS decisions (key TEXT, day TEXT, weights TEXT, first_seen REAL, PRIMARY KEY (key, day));
"""


def kst_day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t + KST))


class Store:
    def __init__(self, path: Path | str = DB_PATH) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
            self.db.commit()

    # ── 쓰기 (엔진) ──
    def add_event(self, ev: dict) -> None:
        with self.lock:
            if ev["type"] == "trade":
                self.db.execute(
                    "INSERT INTO trades (key, coin, source, signal_t, entry_t, exit_signal_t, exit_t, amount, entry_price,"
                    " exit_price, reason, gross_pct, net_pct, net_krw, alt_net_pct, move, lag, detail)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ev["key"], ev["coin"], ev["source"], ev["signal_t"], ev["entry_t"], ev["exit_signal_t"], ev["exit_t"],
                     ev["amount"], ev["entry_price"], ev["exit_price"], ev["reason"], ev["gross_pct"], ev["net_pct"],
                     ev["net_krw"], ev["alt_net_pct"], ev["move"], ev["lag"], json.dumps(ev["detail"])))
            elif ev["type"] == "skip":
                self.db.execute("INSERT INTO skips (key, coin, source, t, why, move, lag) VALUES (?,?,?,?,?,?,?)",
                                (ev["key"], ev["coin"], ev["source"], ev["t"], ev["why"], ev["move"], ev["lag"]))
            elif ev["type"] == "refill":
                self.db.execute("INSERT INTO refills (key, source, t, amount) VALUES (?,?,?,?)",
                                (ev["key"], ev["source"], ev["t"], ev["amount"]))
            elif ev["type"] == "radar":
                self.db.execute(
                    "INSERT INTO radar (coin, source, t0, move, lag, ref_ask, ask0, bid0, spread, points)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ev["coin"], ev["source"], ev["t0"], ev["move"], ev["lag"], ev["ref_ask"], ev["ask0"], ev["bid0"],
                     ev["spread"], json.dumps(ev["points"])))
            self.db.commit()

    def add_cost(self, t: float, coin: str, exchange: str, spread: float, rt1, rt5, rt10) -> None:
        with self.lock:
            self.db.execute("INSERT INTO costs (t, coin, exchange, spread, rt1, rt5, rt10) VALUES (?,?,?,?,?,?,?)",
                            (t, coin, exchange, spread, rt1, rt5, rt10))
            self.db.commit()

    def heartbeat(self, t: float, source: str, gap: float = 90.0) -> None:
        """켜져 있던 시간 기록. 직전 기록에서 gap 초 안이면 이어 붙이고, 아니면 새 구간."""
        with self.lock:
            row = self.db.execute("SELECT id, last, source FROM uptime ORDER BY id DESC LIMIT 1").fetchone()
            if row and row["source"] == source and 0 <= t - row["last"] <= gap:
                self.db.execute("UPDATE uptime SET last=? WHERE id=?", (t, row["id"]))
            else:
                self.db.execute("INSERT INTO uptime (start, last, source) VALUES (?,?,?)", (t, t, source))
            self.db.commit()

    def realized(self, key: str) -> float:
        """이 전략이 지금까지 번 돈(원) + 다시 채운 돈. 다시 켰을 때 남은 돈을 되살리는 데 쓴다."""
        with self.lock:
            won = self.db.execute("SELECT COALESCE(SUM(net_krw), 0) FROM trades WHERE key=?", (key,)).fetchone()[0]
            added = self.db.execute("SELECT COALESCE(SUM(amount), 0) FROM refills WHERE key=?", (key,)).fetchone()[0]
            return won + added

    # ── 느린 전략 보조 기록 ──
    def add_funding(self, series, source: str) -> None:
        """받은 펀딩비를 쌓아 둔다(거래소가 오래된 기록을 지워도 다시 계산할 수 있게). 이미 있는 시각은 그대로 둔다."""
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO funding (t, rate, source) VALUES (?,?,?)",
                                [(ts.timestamp(), float(v), source) for ts, v in series.items()])
            self.db.commit()

    def funding(self, source: str):
        import pandas as pd

        rows = self._rows("SELECT t, rate FROM funding WHERE source=? ORDER BY t", (source,))
        return pd.Series({pd.Timestamp(r["t"], unit="s"): r["rate"] for r in rows}, dtype=float)

    def freeze_decisions(self, key: str, decisions: dict[str, dict], t: float | None = None) -> dict:
        """결정을 처음 본 순간의 값으로 저장하고, 지금 다시 계산한 값과 다른 날을 돌려준다.

        다시 계산하는 방식이라 자료가 나중에 고쳐지면 지나간 결정이 바뀔 수 있다 → 판정은 저장된 결정으로 하고, 어긋나면 화면에 표시.
        """
        t = time.time() if t is None else t
        with self.lock:
            saved = {r["day"]: (json.loads(r["weights"]), r["first_seen"]) for r in self.db.execute(
                "SELECT day, weights, first_seen FROM decisions WHERE key=?", (key,)).fetchall()}
            new = [(key, d, json.dumps(w, sort_keys=True), t) for d, w in decisions.items() if d not in saved]
            if new:
                self.db.executemany("INSERT INTO decisions (key, day, weights, first_seen) VALUES (?,?,?,?)", new)
                self.db.commit()
        changed = sorted(d for d, (w, _) in saved.items() if d in decisions and not _same(w, decisions[d]))
        return {"n": len(saved) + len(new), "changed": changed}

    # ── 읽기 (화면) ──
    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def sources(self) -> list[str]:
        return [r[0] for r in self._rows("SELECT source FROM uptime GROUP BY source ORDER BY MAX(last) DESC")]

    def uptime(self, source: str | None = None) -> dict:
        rows = self._rows("SELECT start, last, source FROM uptime" + (" WHERE source=?" if source else ""),
                          (source,) if source else ())
        total = sum(r["last"] - r["start"] for r in rows)
        return {"seconds": total, "first": min((r["start"] for r in rows), default=None),
                "last": max((r["last"] for r in rows), default=None), "segments": len(rows)}

    def scalp_summary(self, key: str, source: str, capital: float = 10_000_000) -> dict:
        rows = self._rows("SELECT signal_t, exit_t, coin, reason, gross_pct, net_pct, net_krw, alt_net_pct FROM trades"
                          " WHERE key=? AND source=? ORDER BY exit_t", (key, source))
        refills = self._rows("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM refills WHERE key=? AND source=?",
                             (key, source))[0]
        out = {"key": key, "source": source, "n": len(rows), "capital": capital, "refills": refills[0],
               "value": capital + sum(r["net_krw"] for r in rows), "n_events": 0}
        if not rows:
            return out
        net = [r["net_pct"] for r in rows]
        gross = [r["gross_pct"] for r in rows]
        alt = [r["alt_net_pct"] for r in rows if r["alt_net_pct"] is not None]
        by_day: dict[str, list[float]] = {}
        for r in rows:
            by_day.setdefault(kst_day(r["exit_t"]), []).append(r["net_pct"])
        # 채점은 '사건' 단위: 신호가 EVENT_GAP 초 안에 이어진 거래들은 하나로 묶어 평균 낸다
        events = group_events(rows)
        ev_net = [e["net"] for e in events]
        ev_day: dict[str, list[float]] = {}
        for e in events:
            ev_day.setdefault(e["day"], []).append(e["net"])
        top_days = sorted(ev_day, key=lambda d: sum(ev_day[d]), reverse=True)[:3]
        rest = [x for d, v in ev_day.items() if d not in top_days for x in v]
        half = len(ev_net) // 2
        reasons: dict[str, dict] = {}
        coins: dict[str, dict] = {}
        for r in rows:
            for group, k in ((reasons, r["reason"]), (coins, r["coin"])):
                g = group.setdefault(k, {"n": 0, "sum": 0.0})
                g["n"] += 1
                g["sum"] += r["net_pct"]
        step = max(1, len(rows) // 240)
        cum, curve = 0.0, []
        for i, r in enumerate(rows):
            cum += r["net_krw"]
            if i % step == 0 or i == len(rows) - 1:
                curve.append([round(r["exit_t"]), round(cum)])
        today = kst_day(time.time())
        out.update(
            mean_net=_mean(net), median_net=sorted(net)[len(net) // 2], mean_gross=_mean(gross),
            win_rate=sum(x > 0 for x in net) / len(net), t=_t(ev_net), t_trades=_t(net), n_events=len(events),
            mean_event=_mean(ev_net), net_krw=sum(r["net_krw"] for r in rows),
            mean_alt=_mean(alt) if alt else None, n_alt=len(alt), days=len(by_day),
            mean_ex_top3=_mean(rest) if rest else None, top_days=top_days,
            first_half=_mean(ev_net[:half]) if half else None, second_half=_mean(ev_net[half:]) if half else None,
            reasons={k: {"n": v["n"], "mean": v["sum"] / v["n"]} for k, v in reasons.items()},
            coins={k: {"n": v["n"], "mean": v["sum"] / v["n"]} for k, v in coins.items()},
            curve=curve, today=len(by_day.get(today, [])), first_t=rows[0]["exit_t"], last_t=rows[-1]["exit_t"],
        )
        return out

    def trades(self, key: str | None = None, source: str | None = None, limit: int = 50, before: int | None = None) -> list[dict]:
        sql, args = "SELECT id, key, coin, source, signal_t, entry_t, exit_t, amount, entry_price, exit_price, reason," \
                    " gross_pct, net_pct, net_krw, alt_net_pct, move, lag FROM trades WHERE 1=1", []
        for col, val in (("key", key), ("source", source)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        if before:
            sql += " AND id<?"
            args.append(before)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        return [dict(r) for r in self._rows(sql, tuple(args))]

    def trade(self, trade_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM trades WHERE id=?", (trade_id,))
        if not rows:
            return None
        d = dict(rows[0])
        d["detail"] = json.loads(d["detail"] or "{}")
        return d

    def skips(self, source: str, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT key, coin, t, why, move, lag FROM skips WHERE source=?"
                                            " ORDER BY id DESC LIMIT ?", (source, limit))]

    def radar_summary(self, source: str, limit: int = 5000) -> dict:
        """해외가 3초에 +0.15% 넘게 움직인 뒤 업비트 매도 1호가가 그 움직임의 몇 %를 따라왔나 (시간별 평균)."""
        rows = self._rows("SELECT coin, t0, move, lag, ref_ask, ask0, points FROM radar WHERE source=?"
                          " ORDER BY id DESC LIMIT ?", (source, limit))
        out = {"source": source, "n": len(rows)}
        if not rows:
            return out
        from autotrade.paper.scalp import RADAR_OFFSETS

        by_day: dict[str, int] = {}
        per: dict[float, list[tuple[str, float, float]]] = {0.0: []}
        for r in rows:
            day = kst_day(r["t0"])
            by_day[day] = by_day.get(day, 0) + 1
            per[0.0].append((day, (r["ask0"] / r["ref_ask"] - 1) / r["move"], 0.0))
            for nominal, pt in zip(RADAR_OFFSETS, json.loads(r["points"])):
                per.setdefault(nominal, []).append((day, (pt["ask"] / r["ref_ask"] - 1) / r["move"], pt["at"]))
        top_day = max(by_day, key=by_day.get)
        curve = []
        for off in sorted(per):
            vals = per[off]
            rest = [v for d, v, _ in vals if d != top_day]
            curve.append({"offset": off, "n": len(vals), "catch": _mean([v for _, v, _ in vals]),
                          "catch_median": sorted(v for _, v, _ in vals)[len(vals) // 2],
                          "catch_ex_top_day": _mean(rest) if rest else None,
                          "actual": _mean([a for _, _, a in vals])})
        out.update(curve=curve, days=len(by_day), top_day=top_day, top_day_n=by_day[top_day],
                   mean_move=_mean([r["move"] for r in rows]), mean_lag0=_mean([r["lag"] for r in rows]),
                   coins={c: sum(1 for r in rows if r["coin"] == c) for c in sorted({r["coin"] for r in rows})})
        return out

    def cost_summary(self, since: float | None = None) -> list[dict]:
        rows = self._rows("SELECT coin, exchange, spread, rt1, rt5, rt10 FROM costs WHERE t>=?", (since or 0,))
        groups: dict[tuple[str, str], list] = {}
        for r in rows:
            groups.setdefault((r["exchange"], r["coin"]), []).append(r)
        out = []
        for (ex, coin), rs in sorted(groups.items()):
            out.append({"exchange": ex, "coin": coin, "n": len(rs), "spread": _median([r["spread"] for r in rs]),
                        "rt1": _median([r["rt1"] for r in rs]), "rt5": _median([r["rt5"] for r in rs]),
                        "rt10": _median([r["rt10"] for r in rs])})
        return out


def group_events(rows) -> list[dict]:
    """신호 시각 순으로 정렬해, 직전 신호에서 EVENT_GAP 초 안이면 같은 사건으로 묶는다. 사건 값 = 묶인 거래 순수익의 평균."""
    events: list[dict] = []
    last = None
    for r in sorted(rows, key=lambda r: r["signal_t"]):
        if last is None or r["signal_t"] - last > EVENT_GAP:
            events.append({"t": r["signal_t"], "day": kst_day(r["signal_t"]), "nets": []})
        events[-1]["nets"].append(r["net_pct"])
        last = r["signal_t"]
    for e in events:
        e["net"] = _mean(e.pop("nets"))
    return events


def _same(a: dict, b: dict, tol: float = 1e-3) -> bool:
    return all(abs(a.get(k, 0.0) - b.get(k, 0.0)) <= tol for k in set(a) | set(b))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _median(xs: list) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    return xs[len(xs) // 2] if xs else None


def _t(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m / math.sqrt(var / n) if var > 0 else None


def write_json(path: Path, data: dict) -> None:
    """읽는 쪽이 반쯤 쓰인 파일을 보지 않도록 임시 파일에 쓴 뒤 바꿔치기."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
