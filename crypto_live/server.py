"""코인 시세 분석 앱 — 로컬 서버 (파이썬 표준 라이브러리만 사용).

    python crypto_live/server.py          # http://127.0.0.1:8765

- 화면 파일(static/)을 보여 준다.
- 브라우저가 직접 부를 수 없는 거래소 주소(브라우저 보안 정책)를 대신 불러 준다.
  아무 주소나 대신 부르지 않도록 정해 둔 경로만 허용한다.
- 실시간 체결·호가는 브라우저가 업비트 웹소켓에 직접 연결한다(이 서버를 거치지 않음).
- 이 컴퓨터(127.0.0.1)에서만 접속할 수 있다.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
HOST, PORT = "127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765

# 허용하는 경로 → 실제 주소. 그 외 요청은 거절한다.
UPBIT_ALLOWED = re.compile(r"^(candles/minutes/(1|3|5|15|30|60|240)|candles/days|market/all|ticker|orderbook)$")
UPBIT_PARAMS = {"market", "markets", "count", "to", "isDetails"}
COINONE_BASE = re.compile(r"^[A-Z0-9]{1,15}$")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def log_message(self, fmt, *args):  # 요청마다 찍히는 기본 로그는 끈다
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path.startswith("/api/upbit/"):
            return self._upbit(url.path[len("/api/upbit/"):], url.query)
        if url.path.startswith("/api/coinone/orderbook/"):
            return self._coinone(url.path[len("/api/coinone/orderbook/"):])
        if url.path.startswith("/api/"):
            return self._json(HTTPStatus.NOT_FOUND, {"error": "알 수 없는 요청입니다"})
        return super().do_GET()

    def _upbit(self, path: str, query: str):
        if not UPBIT_ALLOWED.match(path):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "허용되지 않은 경로입니다"})
        params = {k: v for k, v in urllib.parse.parse_qsl(query) if k in UPBIT_PARAMS}
        target = f"https://api.upbit.com/v1/{path}?{urllib.parse.urlencode(params)}"
        self._relay(target)

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

    def _json(self, status: HTTPStatus, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"코인 시세 분석 앱: http://{HOST}:{PORT}  (끄려면 Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
