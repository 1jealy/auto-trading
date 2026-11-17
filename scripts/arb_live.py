"""차익 모의 체결(시험 가동) 결과를 실시간으로 보는 작은 화면. 기록(arb.db)을 읽기만 한다.

    python scripts/arb_live.py 8770        # http://127.0.0.1:8770  (5초마다 새로 고침)
"""
from __future__ import annotations

import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autotrade.arb_view import page  # noqa: E402

class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        try:
            data = page().encode()
            self.send_response(200)
        except Exception as e:  # noqa: BLE001
            data = f"오류: {e}".encode()
            self.send_response(500)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
