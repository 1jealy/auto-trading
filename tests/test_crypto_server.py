"""코인 모니터 서버의 PIN 잠금과 주소 제한 테스트 (거래소에는 접속하지 않음).

다른 기기에서 접속한 것처럼 보이게 _is_local 을 바꿔서, 실제로 와이파이에 열지 않고 확인한다.
"""

from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def remote_server(monkeypatch):
    spec = importlib.util.spec_from_file_location("cl_server", ROOT / "crypto_live" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 인자 없이 불러오면 LAN=False 라 PIN 파일을 만들지 않음
    monkeypatch.setattr(mod, "LAN", True)
    monkeypatch.setattr(mod, "PIN", "246810")
    monkeypatch.setattr(mod.Handler, "_is_local", lambda self: False)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def get(url, cookie=None):
    req = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
    try:
        with OPENER.open(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def login(base, pin):
    data = urllib.parse.urlencode({"pin": pin, "next": "/account.html"}).encode()
    try:
        with OPENER.open(urllib.request.Request(f"{base}/login", data=data)) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def test_pages_and_api_locked_without_pin(remote_server):
    _, base = remote_server
    status, headers, _ = get(f"{base}/account.html")
    assert status == 303 and headers["Location"].startswith("/login.html")
    assert get(f"{base}/api/account")[0] == 401
    assert get(f"{base}/login.html")[0] == 200  # PIN 화면은 열려 있어야 함
    assert get(f"{base}/styles.css")[0] == 200


def test_wrong_pin_rejected_and_lockout(remote_server):
    mod, base = remote_server
    status, headers = login(base, "000000")
    assert status == 303 and "err=wrong" in headers["Location"] and "Set-Cookie" not in headers
    for _ in range(8):
        login(base, "000000")
    status, headers = login(base, "246810")  # 맞는 PIN 이어도 잠긴 동안은 거절
    assert "err=locked" in headers["Location"]


def test_correct_pin_gives_session(remote_server):
    _, base = remote_server
    status, headers = login(base, "246810")
    assert status == 303 and headers["Location"] == "/account.html"
    cookie = headers["Set-Cookie"].split(";")[0]
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    status, _, body = get(f"{base}/api/account", cookie)
    assert status == 200 and b"events" in body
    assert get(f"{base}/api/account", "cl_session=guessed-token")[0] == 401


def test_only_public_quote_paths_are_relayed(remote_server):
    _, base = remote_server
    _, headers = login(base, "246810")
    cookie = headers["Set-Cookie"].split(";")[0]
    for path in ["/api/upbit/accounts", "/api/upbit/orders", "/api/upbit/withdraws", "/api/coinone/orderbook/btc%3Brm"]:
        assert get(f"{base}{path}", cookie)[0] == 403, path


def test_open_redirect_blocked(remote_server):
    _, base = remote_server
    data = urllib.parse.urlencode({"pin": "246810", "next": "//evil.example.com"}).encode()
    try:
        with OPENER.open(urllib.request.Request(f"{base}/login", data=data)) as r:
            loc = r.headers["Location"]
    except urllib.error.HTTPError as e:
        loc = e.headers["Location"]
    assert loc == "/account.html"


@pytest.fixture()
def public_server(monkeypatch):
    """--public: 터널 뒤라 모든 접속이 127.0.0.1 로 보이는 상황."""
    spec = importlib.util.spec_from_file_location("cl_server_pub", ROOT / "crypto_live" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LAN", True)
    monkeypatch.setattr(mod, "PUBLIC", True)
    monkeypatch.setattr(mod, "PIN", "135792468024")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)  # _is_local 은 그대로 True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield mod, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_public_mode_asks_code_even_from_localhost(public_server):
    _, base = public_server
    status, _, _ = get(f"{base}/api/paper/summary")
    assert status == 401
    status, headers = login(base, "135792468024")
    assert status == 303
    assert "Secure" in headers["Set-Cookie"] and "HttpOnly" in headers["Set-Cookie"]
    cookie = headers["Set-Cookie"].split(";")[0]
    assert get(f"{base}/api/server", cookie)[0] == 200


def test_public_code_is_long(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("cl_server_pin", ROOT / "crypto_live" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "PIN_FILE", tmp_path / ".pin")
    monkeypatch.delenv("CRYPTO_LIVE_PIN", raising=False)
    (tmp_path / ".pin").write_text("123456")  # 예전 6자리는 공개 모드에서 다시 만든다
    pin = mod.load_pin(12)
    assert len(pin) == 12 and pin.isdigit() and (tmp_path / ".pin").read_text() == pin
