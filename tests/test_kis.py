"""한국투자증권 API 코드 테스트 — 실제 서버에 접속하지 않는 가짜 응답으로 확인."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import autotrade.kis as kis
from autotrade.kis import KISClient, KISError, minute_bars_for_day


class FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, pages=None, token_body=None):
        self.pages = list(pages or [])
        self.token_body = token_body or {
            "access_token": "TOKEN",
            "access_token_token_expired": (datetime.now() + timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.posts, self.gets = 0, []

    def post(self, url, data, headers, timeout):
        self.posts += 1
        return FakeResp(self.token_body)

    def get(self, url, headers, params, timeout):
        self.gets.append(params)
        return FakeResp(self.pages.pop(0))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(kis.time, "sleep", lambda s: None)


def client(tmp_path, session):
    return KISClient("KEY", "SECRET", token_path=tmp_path / "t.json", session=session)


def bar(day, hhmmss, px=100.0):
    return dict(stck_bsop_date=day, stck_cntg_hour=hhmmss, stck_oprc=px, stck_hgpr=px,
                stck_lwpr=px, stck_prpr=px, cntg_vol=10)


def minutes_back(day, start_hhmm, n):
    """start 시각부터 1분씩 거꾸로 n개 (API 가 최신부터 주는 순서)."""
    t = datetime.strptime(day + start_hhmm, "%Y%m%d%H%M")
    return [bar(day, (t - timedelta(minutes=i)).strftime("%H%M%S")) for i in range(n)]


def ok(rows):
    return {"rt_cd": "0", "msg_cd": "", "msg1": "", "output2": rows}


def test_token_is_saved_and_reused(tmp_path):
    s = FakeSession()
    c1 = client(tmp_path, s)
    assert c1.token() == "TOKEN"
    c2 = client(tmp_path, s)  # 새 프로그램 실행이라고 가정
    assert c2.token() == "TOKEN"
    assert s.posts == 1  # 알림톡이 가는 토큰 발급은 한 번만
    saved = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert "SECRET" not in json.dumps(saved)


def test_expired_token_is_reissued(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps(
        {"token": "OLD", "expires": "2000-01-01 00:00:00", "server": kis.REAL_URL}), encoding="utf-8")
    s = FakeSession()
    assert client(tmp_path, s).token() == "TOKEN" and s.posts == 1


def test_day_is_paged_backwards_until_market_open(tmp_path):
    day = "20261113"
    pages = [ok(minutes_back(day, "1530", 120)), ok(minutes_back(day, "1330", 120)),
             ok(minutes_back(day, "1130", 120)), ok(minutes_back(day, "0930", 31))]
    s = FakeSession(pages)
    df = minute_bars_for_day(client(tmp_path, s), "069500", day)
    assert [g["FID_INPUT_HOUR_1"] for g in s.gets] == ["153000", "133100", "113100", "093100"]
    assert df.index.min() == datetime(2026, 11, 13, 9, 0)
    assert df.index.max() == datetime(2026, 11, 13, 15, 30)
    assert df.index.is_unique and df.index.is_monotonic_increasing


def test_rows_from_previous_day_are_dropped(tmp_path):
    day = "20261113"
    rows = minutes_back(day, "0905", 6) + minutes_back("20261112", "1530", 114)
    df = minute_bars_for_day(client(tmp_path, FakeSession([ok(rows)])), "069500", day)
    assert len(df) == 6 and (df.index.date == datetime(2026, 11, 13).date()).all()


def test_holiday_returns_empty(tmp_path):
    df = minute_bars_for_day(client(tmp_path, FakeSession([ok([])])), "069500", "20261115")
    assert df.empty


def test_rate_limit_is_retried_then_succeeds(tmp_path):
    busy = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
    s = FakeSession([busy, ok(minutes_back("20261113", "0905", 6))])
    df = minute_bars_for_day(client(tmp_path, s), "069500", "20261113")
    assert len(df) == 6 and len(s.gets) == 2


def test_other_errors_raise(tmp_path):
    bad = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
    with pytest.raises(KISError, match="EGW00123"):
        minute_bars_for_day(client(tmp_path, FakeSession([bad])), "069500", "20261113")


def test_missing_keys_rejected(tmp_path):
    with pytest.raises(KISError, match="KIS_APP_KEY"):
        KISClient("", "", token_path=tmp_path / "t.json", session=FakeSession())
