#!/bin/bash
# 클라우드 세션 안에서 실행: 실험에 필요한 시세 데이터를 다시 받는다 (GitHub 에 올리지 않는 데이터라 클라우드엔 없음).
#   bash scripts/cloud_data.sh crypto   # 업비트 비트코인·이더리움 1분 데이터 (2017-09 ~ 지난달, 약 2시간)
#   bash scripts/cloud_data.sh stocks   # 주식 실험(#1~#4)용 — 실행할 때 자동으로 받으므로 따로 할 일 없음
# 중간에 끊겨도 다시 실행하면 받은 달은 건너뛴다. 오래 걸리니 백그라운드로 돌리는 것을 권장.
set -euo pipefail
case "${1:-crypto}" in
  crypto)
    python3 scripts/download_upbit_minutes.py KRW-BTC KRW-ETH 2017-09
    ;;
  stocks)
    echo "주식 실험 스크립트(scripts/exp01~04, exp03)는 실행할 때 FinanceDataReader 로 필요한 만큼 자동으로 받습니다."
    ;;
  *)
    echo "사용법: bash scripts/cloud_data.sh [crypto|stocks]"; exit 1 ;;
esac
