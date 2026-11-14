"""코인 시세·계좌 모니터 — 로컬 서버 (파이썬 표준 라이브러리만 사용).

    python crypto_live/server.py              # 이 컴퓨터에서만: http://127.0.0.1:8765
    python crypto_live/server.py 8765 --lan   # 같은 와이파이의 폰·패드에서도 접속 (PIN 필요)

- 화면 파일(static/)을 보여 준다.
- 브라우저가 직접 부를 수 없는 거래소 주소(브라우저 보안 정책)를 대신 불러 준다.
  아무 주소나 대신 부르지 않도록 정해 둔 공개 시세 경로만 허용한다.
- 실시간 체결·호가는 브라우저가 업비트 웹소켓에 직접 연결한다(이 서버를 거치지 않음).
- /api/account: 계좌 정보. 지금은 **모의 계좌**(crypto_live/account.json, 없으면 static/mock_account.json).
- --lan 이면 다른 기기는 PIN 을 입력해야 들어올 수 있다. 이 컴퓨터에서 여는 것은 PIN 없이 된다.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
ACCOUNT_FILES = [HERE / "account.json", STATIC / "mock_account.json"]
PIN_FILE = HERE / ".pin"

# 실행할 때 main() 에서 정해진다 (불러오기만 할 때는 이 컴퓨터 전용 기본값)
LAN = False
PORT = 8765

# 허용하는 경로 → 실제 주소. 그 외 요청은 거절한다.
UPBIT_ALLOWED = re.compile(r"^(candles/minutes/(1|3|5|15|30|60|240)|candles/days|market/all|ticker|orderbook)$")
UPBIT_PARAMS = {"market", "markets", "count", "to", "isDetails"}
COINONE_BASE = re.compile(r"^[A-Z0-9]{1,15}$")

SESSION_COOKIE = "cl_session"
SESSION_DAYS = 30
OPEN_PATHS = {"/login", "/login.html", "/styles.css", "/manifest.webmanifest"}
OPEN_PREFIXES = ("/icons/",)


def load_pin() -> str:
    pin = os.environ.get("CRYPTO_LIVE_PIN", "").strip()
    if pin:
        return pin
    if PIN_FILE.exists():
        return PIN_FILE.read_text(encoding="utf-8").strip()
    pin = f"{secrets.randbelow(10**6):06d}"
    PIN_FILE.write_text(pin, encoding="utf-8")
    return pin


PIN = ""
SESSIONS: dict[str, float] = {}  # 토큰 → 만료 시각
FAILS = {"count": 0, "until": 0.0}
LOCK = threading.Lock()


def lan_addresses() -> list[str]:
    addrs = set()
    try:  # 실제로 데이터를 보내지 않고, 밖으로 나갈 때 쓰는 내 주소만 알아낸다
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".webmanifest": "application/manifest+json", ".js": "text/javascript"}

    def log_message(self, fmt, *args):  # 요청마다 찍히는 기본 로그는 끈다
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    # ── 잠금 ──
    def _is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _authed(self) -> bool:
        if not LAN or self._is_local():
            return True
        token = SimpleCookie(self.headers.get("Cookie", "")).get(SESSION_COOKIE)
        if not token:
            return False
        with LOCK:
            exp = SESSIONS.get(token.value, 0)
        return exp > time.time()

    def _guard(self, path: str) -> bool:
        """잠금 통과면 True. 아니면 로그인으로 보내고 False."""
        if self._authed() or path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            return True
        if path.startswith("/api/"):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "PIN 을 먼저 입력해 주세요"})
        else:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/login.html?next=" + urllib.parse.quote(path))
            self.end_headers()
        return False

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/login":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "알 수 없는 요청입니다"})
        length = min(int(self.headers.get("Content-Length", 0) or 0), 2048)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        pin = (form.get("pin") or [""])[0].strip()
        nxt = (form.get("next") or ["/account.html"])[0]
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/account.html"
        with LOCK:
            locked = FAILS["until"] > time.time()
        if locked:
            return self._redirect(f"/login.html?err=locked&next={urllib.parse.quote(nxt)}")
        if LAN and hmac.compare_digest(pin, PIN):
            token = secrets.token_urlsafe(32)
            with LOCK:
                SESSIONS[token] = time.time() + SESSION_DAYS * 86400
                FAILS["count"] = 0
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}={token}; Max-Age={SESSION_DAYS * 86400}; "
                                           "Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Location", nxt)
            self.end_headers()
            return
        time.sleep(1.0)  # 마구 맞혀 보기 늦추기
        with LOCK:
            FAILS["count"] += 1
            if FAILS["count"] >= 8:
                FAILS["until"], FAILS["count"] = time.time() + 300, 0
        self._redirect(f"/login.html?err=wrong&next={urllib.parse.quote(nxt)}")

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if not self._guard(path):
            return
        if path == "/":
            return self._redirect("/account.html")
        if path == "/api/account":
            return self._account()
        if path == "/api/server":
            return self._json(HTTPStatus.OK, {"lan": LAN, "local": self._is_local()})
        if path.startswith("/api/upbit/"):
            return self._upbit(path[len("/api/upbit/"):], url.query)
        if path.startswith("/api/coinone/orderbook/"):
            return self._coinone(path[len("/api/coinone/orderbook/"):])
        if path.startswith("/api/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "알 수 없는 요청입니다"})
        return super().do_GET()

    # ── 계좌 (지금은 모의) ──
    def _account(self):
        for f in ACCOUNT_FILES:
            if f.exists():
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{f.name} 형식 오류: {e}"})
                data["source"] = f.name
                return self._json(HTTPStatus.OK, data)
        return self._json(HTTPStatus.NOT_FOUND, {"error": "계좌 파일이 없습니다"})

    # ── 공개 시세 대신 불러오기 ──
    def _upbit(self, path: str, query: str):
        if not UPBIT_ALLOWED.match(path):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "허용되지 않은 경로입니다"})
        params = {k: v for k, v in urllib.parse.parse_qsl(query) if k in UPBIT_PARAMS}
        self._relay(f"https://api.upbit.com/v1/{path}?{urllib.parse.urlencode(params)}")

    def _coinone(self, base: str):
        if not COINONE_BASE.match(base):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "허용되지 않은 코인 이름입니다"})
        self._relay(f"https://api.coinone.co.kr/public/v2/orderbook/KRW/{base}?size=5")

    def _relay(self, target: str):
        req = urllib.request.Request(target, headers={"Accept": "application/json", "User-Agent": "crypto-live-local"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body, status = resp.read(), resp.status
        except urllib.error.HTTPError as e:
            body, status = e.read(), e.code
        except (urllib.error.URLError, TimeoutError) as e:
            return self._json(HTTPStatus.BAD_GATEWAY, {"error": f"거래소에 연결하지 못했습니다: {e}"})
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def _json(self, status: HTTPStatus, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    global LAN, PORT, PIN
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    LAN = "--lan" in sys.argv
    PORT = int(args[0]) if args else 8765
    PIN = load_pin() if LAN else ""
    server = ThreadingHTTPServer(("0.0.0.0" if LAN else "127.0.0.1", PORT), Handler)
    print(f"이 컴퓨터에서: http://127.0.0.1:{PORT}", flush=True)
    if LAN:
        for a in lan_addresses():
            print(f"폰·패드에서 (같은 와이파이): http://{a}:{PORT}", flush=True)
        print(f"접속 PIN: {PIN}   (crypto_live/.pin 에 저장됨, 바꾸려면 파일을 지우고 다시 실행)", flush=True)
        print("처음 실행 때 윈도우 방화벽 창이 뜨면 '개인 네트워크'만 허용하세요.", flush=True)
    print("끄려면 이 창을 닫거나 Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
