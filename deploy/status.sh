#!/usr/bin/env bash
# 지금 접속 주소, 접속 코드, 엔진 상태를 보여 준다.   bash ~/auto-trading/deploy/status.sh
DIR="${AUTOTRADE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
url=""
for i in $(seq 1 30); do
  url=$(sudo journalctl -u coin-tunnel --no-pager -o cat 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1)
  [ -n "$url" ] || [ "$1" != "--wait" ] && break
  sleep 2
done
code=$(cat "$DIR/crypto_live/.pin" 2>/dev/null)
echo
echo "  화면 서버  : $(systemctl is-active coin-monitor)"
echo "  터널      : $(systemctl is-active coin-tunnel)"
echo "  접속 주소  : ${url:-아직 없음 — 잠시 뒤 다시 실행해 보세요}/paper.html"
echo "  접속 코드  : ${code:-아직 없음}"
echo
echo "  엔진 기록(최근):"
sudo journalctl -u coin-monitor --no-pager -o cat -n 200 2>/dev/null | grep '모의 엔진' | tail -3 | sed 's/^/    /'
echo
echo "  주소는 터널이 다시 시작될 때(서버 재부팅 등) 바뀝니다. 그때 이 명령으로 새 주소를 확인하세요."
