# 허깅페이스 Spaces(Docker, 무료 CPU)에서 코인 모니터 + 모의 투자 엔진을 띄운다. 이 파일 하나만 Space 에 넣으면 된다.
# - 깃허브 공개 저장소의 최신 코드를 받아 실행한다. Space 를 "Restart" 하면 최신 코드로 바뀐다.
# - 공개 시세만 읽고 주문은 내지 않는다. 거래소 키가 필요 없다.
# - Space 설정 → Variables and secrets → Secret 으로 CRYPTO_LIVE_PIN(접속 코드, 숫자 12자리 이상 권장)을 꼭 넣을 것.
# - 한계: 미국 서버라 바이낸스 선물이 막혀 OKX 가격으로 돈다(시험 기록, #13 계산 불가).
#         무료 공간은 다시 시작하면 기록이 지워지고, 48시간 아무도 안 열면 잠든다.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /home/user

RUN git clone --depth 1 https://github.com/1jealy/auto-trading.git app \
 && pip install --no-cache-dir --user -e app

WORKDIR /home/user/app
EXPOSE 7860
CMD git pull -q --ff-only || true; \
    pip install --no-cache-dir --user -q -e . ; \
    exec python crypto_live/server.py 7860 --public --host=0.0.0.0 --engine
