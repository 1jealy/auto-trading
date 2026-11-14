"""한국투자증권 Open API — 시세 조회만 (주문 기능 없음).

공식 예제(github.com/koreainvestment/open-trading-api)를 참고해 필요한 부분만 옮겼다.
- 접근 토큰은 하루 유효. 새로 받을 때마다 알림톡이 가므로 파일에 저장해 두고 재사용한다.
- 과거 1분 데이터(주식일별분봉조회, FHKST03010230)는 최대 1년 치, 한 번에 최대 120개.
- 앱 키·시크릿은 .env 에서만 읽는다. 코드·로그·커밋에 남기지 않는다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

REAL_URL = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"
MINUTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
MINUTE_TR = "FHKST03010230"
RATE_LIMIT_CODE = "EGW00201"
MINUTE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class KISError(RuntimeError):
    pass


@dataclass
class KISClient:
    app_key: str
    app_secret: str
    paper: bool = False  # True 면 모의투자 서버 (모의투자용 키가 따로 있음)
    token_path: Path = Path(".kis_token.json")
    pause: float = 0.1  # 요청 사이 쉬는 시간(초). 모의투자 서버는 더 길게.
    session: Any = None  # 테스트용으로 바꿔 끼울 수 있게
    _token: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.app_key or not self.app_secret:
            raise KISError("앱 키/시크릿이 비어 있습니다. .env 의 KIS_APP_KEY, KIS_APP_SECRET 을 확인하세요.")
        if self.session is None:
            import requests

            self.session = requests.Session()

    @property
    def base_url(self) -> str:
        return PAPER_URL if self.paper else REAL_URL

    # ── 토큰 ─────────────────────────────────────────────

    def token(self) -> str:
        if self._token:
            return self._token
        cached = self._read_cached_token()
        if cached:
            self._token = cached
            return cached

        resp = self.session.post(
            f"{self.base_url}/oauth2/tokenP",
            data=json.dumps({"grant_type": "client_credentials",
                             "appkey": self.app_key, "appsecret": self.app_secret}),
            headers={"content-type": "application/json"},
            timeout=10,
        )
        body = resp.json()
        if resp.status_code != 200 or "access_token" not in body:
            raise KISError(f"토큰 발급 실패: {body.get('error_description') or body.get('msg1') or resp.status_code}")
        self._token = body["access_token"]
        self._save_token(self._token, body.get("access_token_token_expired", ""))
        return self._token

    def _read_cached_token(self) -> str | None:
        if not self.token_path.exists():
            return None
        try:
            saved = json.loads(self.token_path.read_text(encoding="utf-8"))
            expires = datetime.strptime(saved["expires"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError, json.JSONDecodeError):
            return None
        if saved.get("server") != self.base_url or expires - datetime.now() < timedelta(minutes=10):
            return None
        return saved["token"]

    def _save_token(self, token: str, expires: str) -> None:
        if not expires:
            expires = (datetime.now() + timedelta(hours=23)).strftime("%Y-%m-%d %H:%M:%S")
        self.token_path.write_text(
            json.dumps({"token": token, "expires": expires, "server": self.base_url}), encoding="utf-8"
        )

    # ── 요청 ─────────────────────────────────────────────

    def get(self, path: str, tr_id: str, params: dict, retries: int = 3) -> dict:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        wait = 1.0
        for attempt in range(retries + 1):
            resp = self.session.get(f"{self.base_url}{path}", headers=headers, params=params, timeout=10)
            body = resp.json()
            if body.get("rt_cd") == "0":
                time.sleep(self.pause)
                return body
            if body.get("msg_cd") == RATE_LIMIT_CODE and attempt < retries:
                time.sleep(wait)  # 초당 요청 한도 초과 → 잠시 쉬고 다시
                wait *= 4
                continue
            raise KISError(f"{tr_id} 실패: [{body.get('msg_cd')}] {body.get('msg1')}")
        raise KISError(f"{tr_id} 실패: 요청 한도 초과가 계속됨")


def minute_bars_for_day(client: KISClient, code: str, day: str, max_pages: int = 10) -> pd.DataFrame:
    """하루치 1분 데이터. day 는 'YYYYMMDD'. 장 마감 시각부터 거꾸로 120개씩 받는다.

    인덱스는 한국 시간(체결 시각), 열은 Open/High/Low/Close/Volume. 그날 데이터가 없으면 빈 표.
    """
    rows: list[dict] = []
    hour = "153000"
    for _ in range(max_pages):
        body = client.get(MINUTE_PATH, MINUTE_TR, {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hour,
            "FID_INPUT_DATE_1": day,
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "",
        })
        page = [r for r in body.get("output2") or [] if r.get("stck_cntg_hour")]
        same_day = [r for r in page if r.get("stck_bsop_date", day) == day]
        rows.extend(same_day)
        if not page or len(same_day) < len(page):  # 전날로 넘어감 → 그날 끝
            break
        earliest = min(r["stck_cntg_hour"] for r in page)
        if earliest <= "090000" or len(page) < 120:
            break
        hour = earliest

    if not rows:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    df = pd.DataFrame({
        "Datetime": pd.to_datetime([day + r["stck_cntg_hour"] for r in rows], format="%Y%m%d%H%M%S"),
        "Open": [float(r["stck_oprc"]) for r in rows],
        "High": [float(r["stck_hgpr"]) for r in rows],
        "Low": [float(r["stck_lwpr"]) for r in rows],
        "Close": [float(r["stck_prpr"]) for r in rows],
        "Volume": [float(r["cntg_vol"]) for r in rows],
    }).drop_duplicates("Datetime").set_index("Datetime").sort_index()
    return df
