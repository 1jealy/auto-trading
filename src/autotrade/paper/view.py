"""화면에 보낼 요약 만들기 (기록 읽기 전용)."""

from __future__ import annotations

import time

from autotrade.paper.feeds import SOURCE_NAMES
from autotrade.paper.news import NEWS_PATH
from autotrade.paper.scalp import ALWAYS, FORMAL
from autotrade.paper.store import LIVE_PATH, OFFICIAL_SOURCE, SLOW_PATH, Store, read_json

RULES = {
    "s17": {"move": FORMAL.move, "lag": FORMAL.lag, "amount": FORMAL.amount, "max_hold": FORMAL.max_hold,
            "take": FORMAL.take, "stop": FORMAL.stop, "fee": FORMAL.fee, "min_events": 100},
    "s18": {"move": ALWAYS.move, "cost_gap": ALWAYS.cost_gap, "amount": ALWAYS.amount, "max_hold": ALWAYS.max_hold,
            "fee": ALWAYS.fee, "min_events": 200},
}


def build_summary(store: Store, now: float | None = None) -> dict:
    now = now or time.time()
    live = read_json(LIVE_PATH)
    alive = bool(live and not live.get("stopped") and now - live.get("t", 0) < 20)
    sources = store.sources()
    source = (live or {}).get("source") or (sources[0] if sources else OFFICIAL_SOURCE)
    scalp = {}
    for p in (FORMAL, ALWAYS):
        s = store.scalp_summary(p.key, source, p.capital)
        s["rules"] = RULES[p.key]
        scalp[p.key] = s
    return {
        "now": now,
        "engine": {"alive": alive, "last": (live or {}).get("t"), "started": (live or {}).get("started"),
                   "source": source, "source_name": SOURCE_NAMES.get(source, source),
                   "official": source == OFFICIAL_SOURCE, "loop_ms": (live or {}).get("loop_ms"),
                   "errors": (live or {}).get("errors") or {}, "other_sources": [s for s in sources if s != source]},
        "uptime": store.uptime(source),
        "scalp": scalp,
        "live": (live or {}).get("scalp") if alive else None,
        "radar": store.radar_summary(source),
        "costs": store.cost_summary(now - 7 * 86400),
        "skips": store.skips(source, 20),
        "slow": read_json(SLOW_PATH),
        "news": read_json(NEWS_PATH),
    }
