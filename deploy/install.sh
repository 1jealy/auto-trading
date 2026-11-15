#!/usr/bin/env bash
# 코인 모니터 + 모의 투자 엔진을 우분투 서버(무료 클라우드 VM 등)에 설치하고 계속 켜 둔다.
#
#   curl -fsSL https://raw.githubusercontent.com/1jealy/auto-trading/main/deploy/install.sh | bash
#
# 다시 실행하면 최신 코드로 바꾸고 다시 시작한다(기록 data/paper/ 는 그대로).
# - 공개 시세만 읽고 주문은 내지 않는다. 키·비밀 정보가 필요 없다.
# - 화면은 127.0.0.1 에서만 듣고, 밖에서는 Cloudflare 무료 터널(https://….trycloudflare.com)로 들어온다.
# - 모든 접속에 12자리 접속 코드를 묻는다.
set -euo pipefail

REPO="https://github.com/1jealy/auto-trading.git"
DIR="${AUTOTRADE_DIR:-$HOME/auto-trading}"
PORT=8765
USER_NAME="$(id -un)"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

if ! command -v apt-get >/dev/null; then
  echo "우분투(또는 데비안) 서버에서 실행해 주세요."; exit 1
fi

say "1/6 필요한 프로그램 설치"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv python3-pip curl >/dev/null

say "2/6 메모리가 작으면 스왑(보조 메모리) 2GB 만들기"
mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 2000000 ] && ! swapon --show | grep -q .; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "스왑 2GB 켬"
else
  echo "필요 없음"
fi

say "3/6 코드 받기 → $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin main && git -C "$DIR" checkout -q main && git -C "$DIR" reset -q --hard origin/main
else
  git clone -q "$REPO" "$DIR"
fi

say "4/6 파이썬 패키지 설치 (처음엔 몇 분 걸려요)"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -e "$DIR"

say "5/6 Cloudflare 무료 터널 프로그램 설치"
if ! command -v cloudflared >/dev/null; then
  case "$(dpkg --print-architecture)" in
    amd64) arch=amd64 ;; arm64) arch=arm64 ;; *) echo "지원하지 않는 CPU"; exit 1 ;;
  esac
  curl -fsSL -o /tmp/cloudflared.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  sudo dpkg -i /tmp/cloudflared.deb >/dev/null
fi

say "6/6 계속 켜 두기 (서버를 다시 켜도 자동으로 시작)"
sudo tee /etc/systemd/system/coin-monitor.service >/dev/null <<UNIT
[Unit]
Description=Coin monitor + paper-trading engine (no real orders)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$DIR/.venv/bin/python crypto_live/server.py $PORT --public --engine
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo tee /etc/systemd/system/coin-tunnel.service >/dev/null <<UNIT
[Unit]
Description=Cloudflare quick tunnel to the coin monitor
After=coin-monitor.service network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable -q coin-monitor coin-tunnel
sudo systemctl restart coin-monitor coin-tunnel

bash "$DIR/deploy/status.sh" --wait
