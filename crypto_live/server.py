"""코인 시세·계좌 모니터 — 로컬 서버 (파이썬 표준 라이브러리만 사용).

    python crypto_live/server.py              # 이 컴퓨터에서만: http://127.0.0.1:8765
    python crypto_live/server.py 8765 --lan   # 같은 와이파이의 폰·패드에서도 접속 (PIN 필요)

- 화면 파일(static/)을 보여 준다.
- 브라우저가 직접 부를 수 없는 거래소 주소(브라우저 보안 정책)를 대신 불러 준다.
  아무 주소나 대신 부르지 않도록 정해 둔 공개 시세 경로만 허용한다.
- 실시간 체결·호가는 브라우저가 업비트 웹소켓에 직접 연결한다(이 서버를 거치지 않음).
- /api/account: 계좌 정보. 지금은 **모의 계좌**(crypto_live/account.json, 없으면 static/mock_account.json).
- --lan 이면 다른 기기는 PIN 을 입력해야 들어올 수 있다. 이 컴퓨터에서 여는 것은 PIN 없이 된다.
- --public 은 인터넷 서버용(HTTPS 터널 뒤): **모든 접속에 접속 코드를 묻는다**(터널을 거치면 모두 이 컴퓨터에서 온 것처럼
  보이므로 "이 컴퓨터면 통과"를 끈다). 코드는 12자리로 길게 만들고, 쿠키는 HTTPS 에서만 보낸다. 127.0.0.1 에서만 듣는다.
  호스팅이 바깥 연결을 직접 넘겨주는 곳(허깅페이스 Spaces 등)에서는 --host=0.0.0.0 으로 듣는 주소를 바꾼다.
  접속 코드는 환경 변수 CRYPTO_LIVE_PIN 으로 정할 수 있다(다시 시작해도 같은 코드).
- /api/paper/*: 모의 투자 기록(data/paper/)을 읽어 보여 준다. --engine 을 붙이면 모의 투자 엔진(src/autotrade/paper)도
  이 창 안에서 함께 돈다. 엔진은 공개 시세만 읽고 주문은 내지 않는다.
- --arb: 국내 거래소 차익 관찰(scripts/obs_arbitrage.py)도 함께 돌리고 /arb 에서 결과를 보여 준다(접속 코드 필요).
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
ROOT = HERE.parent
STATIC = HERE / "static"
ACCOUNT_FILES = [HERE / "account.json", STATIC / "mock_account.json"]
PIN_FILE = HERE / ".pin"

# 실행할 때 main() 에서 정해진다 (불러오기만 할 때는 이 컴퓨터 전용 기본값)
LAN = False
PUBLIC = False
PORT = 8765

# 허용하는 경로 → 실제 주소. 그 외 요청은 거절한다.
UPBIT_ALLOWED = re.compile(r"^(candles/minutes/(1|3|5|15|30|60|240)|candles/days|market/all|ticker|orderbook)$")
UPBIT_PARAMS = {"market", "markets", "count", "to", "isDetails"}
COINONE_BASE = re.compile(r"^[A-Z0-9]{1,15}$")

SESSION_COOKIE = "cl_session"
SESSION_DAYS = 30
OPEN_PATHS = {"/login", "/login.html", "/styles.css", "/manifest.webmanifest"}
OPEN_PREFIXES = ("/icons/",)


def load_pin(length: int = 6) -> str:
    pin = os.environ.get("CRYPTO_LIVE_PIN", "").strip()
    if pin:
        return pin
    if PIN_FILE.exists():
        saved = PIN_FILE.read_text(encoding="utf-8").strip()
        if len(saved) >= length:
            return saved
    pin = "".join(secrets.choice("0123456789") for _ in range(length))
    PIN_FILE.write_text(pin, encoding="utf-8")
    return pin


PAPER = {"store": None, "summary": None, "at": 0.0}


def paper_store():
    """모의 투자 기록 저장소. 처음 쓸 때 연다(엔진 꾸러미는 src/ 에 있다)."""
    if PAPER["store"] is None:
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        from autotrade.paper.store import Store

        PAPER["store"] = Store()
    return PAPER["store"]


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
        if not LAN or (self._is_local() and not PUBLIC):
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
                                           "Path=/; HttpOnly; SameSite=Strict" + ("; Secure" if PUBLIC else ""))
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
            return self._redirect("/paper.html")
        if path in ("/arb", "/arb.html"):
            return self._arb()
        if path == "/api/account":
            return self._account()
        if path == "/api/server":
            return self._json(HTTPStatus.OK, {"lan": LAN, "local": self._is_local() and not PUBLIC})
        if path.startswith("/api/paper/"):
            return self._paper(path[len("/api/paper/"):], dict(urllib.parse.parse_qsl(url.query)))
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

    # ── 모의 투자 기록 (읽기 전용) ──
    def _arb(self):
        """국내 거래소 차익 모의 체결(시험 가동) 화면. 접속 코드 뒤에 있다(_guard)."""
        try:
            paper_store()  # src 경로 준비
            from autotrade.arb_view import page

            body, code = page().encode(), HTTPStatus.OK
        except Exception as e:  # noqa: BLE001
            body, code = f"차익 기록을 읽지 못했습니다: {e}".encode(), HTTPStatus.INTERNAL_SERVER_ERROR
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _paper(self, what: str, q: dict):
        try:
            store = paper_store()
            if what == "summary":
                with LOCK:
                    fresh = PAPER["summary"] is not None and time.time() - PAPER["at"] < 1.5
                if not fresh:
                    from autotrade.paper.view import build_summary

                    data = build_summary(store)
                    with LOCK:
                        PAPER["summary"], PAPER["at"] = data, time.time()
                return self._json(HTTPStatus.OK, PAPER["summary"])
            if what == "trades":
                key = q.get("key") if q.get("key") in ("s17", "s18", "s21") else None
                before = int(q["before"]) if q.get("before", "").isdigit() else None
                limit = int(q["limit"]) if q.get("limit", "").isdigit() else 50
                source = q.get("source") if re.fullmatch(r"[a-z_]{1,20}", q.get("source", "")) else None
                return self._json(HTTPStatus.OK, {"trades": store.trades(key, source, limit, before)})
            if what == "trade" and q.get("id", "").isdigit():
                tr = store.trade(int(q["id"]))
                if tr is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "그런 거래 기록이 없습니다"})
                return self._json(HTTPStatus.OK, tr)
        except Exception as e:  # noqa: BLE001
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"모의 투자 기록을 읽지 못했습니다: {e}"})
        return self._json(HTTPStatus.NOT_FOUND, {"error": "알 수 없는 요청입니다"})

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
    global LAN, PUBLIC, PORT, PIN
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    host_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--host=")), None)
    PUBLIC = "--public" in sys.argv
    LAN = "--lan" in sys.argv or PUBLIC
    PORT = int(args[0]) if args else 8765
    PIN = load_pin(12 if PUBLIC else 6) if LAN else ""
    host = host_arg or ("0.0.0.0" if LAN and not PUBLIC else "127.0.0.1")  # 공개 서버는 기본적으로 터널만 들어올 수 있게
    server = ThreadingHTTPServer((host, PORT), Handler)
    if "--engine" in sys.argv:
        try:
            paper_store()
            from autotrade.paper.run import start_in_thread

            start_in_thread()
            print("모의 투자 엔진: 켜짐 (가짜 돈, 주문 없음). 이 창을 닫으면 단타 기록도 멈춥니다.", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"모의 투자 엔진을 켜지 못했습니다: {e}", flush=True)
    if "--arb" in sys.argv:
        try:
            paper_store()
            sys.path.insert(0, str(ROOT / "scripts"))
            import obs_arbitrage

            obs_arbitrage.start_in_thread()
            print("차익 관찰: 켜짐 (국내 거래소 4곳 호가, 주문 없음). 화면은 /arb", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"차익 관찰을 켜지 못했습니다: {e}", flush=True)
    print(f"이 컴퓨터에서: http://127.0.0.1:{PORT}", flush=True)
    if PUBLIC:
        print(f"접속 코드: {PIN}   (crypto_live/.pin 에 저장됨. 모든 접속에 필요)", flush=True)
        print(f"듣는 주소: {host}:{PORT}. 밖에서는 HTTPS 주소로만 들어오세요(docs/server_setup.md).", flush=True)
    elif LAN:
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
