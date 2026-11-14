#!/bin/bash
# 클라우드 환경의 "설정 스크립트(Setup script)" 칸에 이 내용을 붙여 넣는다.
# 세션이 시작되기 전에 한 번 실행되고, 5분 안에 끝나면 저장(캐시)된다.
# 패키지 설치만 한다. 시세 데이터는 용량·시간이 커서 세션 안에서 scripts/cloud_data.sh 로 받는다.
set -euo pipefail
python3 --version
python3 -m pip install --quiet -e ".[dev]"
python3 -m pytest -q 2>&1 | tail -1 || true
