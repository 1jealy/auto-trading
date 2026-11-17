# auto-trading

개인용 주식 자동매매 연구 프로젝트. 현재 단계는 **백테스트 검증**이며, 실전 주문 기능은 아직 없다.

## 구조

- `src/autotrade/config.py` — 거래 비용 모델(`CostModel`)
- `src/autotrade/data.py` — 가격 데이터(`Prices`: 시가/종가 표), FinanceDataReader 로더 + CSV 캐시
- `src/autotrade/engine.py` — 백테스트 엔진(`run_backtest`)
- `src/autotrade/metrics.py` — 성과 지표(CAGR, 변동성, 샤프, MDD)
- `src/autotrade/strategies/` — 전략 모듈. `Strategy.target_weights(history)`만 구현하면 된다
- `tests/` — 엔진 정확성 테스트(미래 참조 금지, 비용 반영, 체결 시점)
- `scripts/` — 실행 예제
- `app/` — 결과 보기 앱(Streamlit). `실행.bat` 또는 `.venv/Scripts/python -m streamlit run app/app.py`
- `src/autotrade/paper/` — 모의 투자(포워드 테스트) 엔진. 공개 시세만 읽고 가짜 체결을 기록한다(주문 기능 없음)
- `crypto_live/` — 코인 모니터(시세·모의 투자 화면). `코인시세.bat` 이 화면 서버와 모의 투자 엔진을 함께 켠다
- `src/autotrade/lots.py` — #22(탈락)·#23(통과, 약함) 덩이 매수·익절·손절·추적 손절 계산(백테스트). `scripts/exp23_adaptive.py`. 자료는 `scripts/exp22_lots.py` 가 `data/cache/exp22/`에 받는다
- `src/autotrade/arb.py`·`scripts/obs_arbitrage.py` — 국내 거래소 차익 틈 관찰·모의 체결(웹소켓, 주문 없음). `python scripts/obs_arbitrage.py run --minutes 60`, 서버는 `server.py --arb` 로 함께 돌리고 `/arb` 에서 봄(`arb_view.py`)
- `src/autotrade/dart.py`·`event_swing.py` — #15 공시 받기·표 읽기, 공시 뒤 N일 보유 계산. 받은 공시는 `data/cache/dart/`(Git 제외)
- `docs/plan.md` — 앞으로의 계획(#12~#18)과 고친 이유. 정확한 규칙은 `docs/experiments.md`

## 결과 보기 앱 (app/)

- 사용자는 쉬운 한국어 화면을 원한다. 전문 용어 대신 "흔들림", "가장 크게 떨어졌을 때", "1년 평균"처럼 쓴다.
- `app/runners.py`의 계산은 `scripts/exp0*.py`의 상수(종목, 기간, 비용)를 그대로 가져다 쓴다. 기본값으로 돌린 결과는
  docs/experiments.md 기록과 같아야 한다. 실험 스크립트를 바꾸면 앱 숫자도 기록과 맞는지 다시 확인할 것.
- **검증 기간은 이미 돌린 실험만 연다**(`view_results.VALIDATION_DONE`). 새 실험의 검증 기간을 돌리면 그때 추가한다.
- "숫자 바꿔 보기"는 연습 기간 전용이며 아무것도 저장하지 않는다. 결과를 보고 규칙을 바꾸면 새 시도로 기록해야 한다.
- `.streamlit/config.toml`: 이 컴퓨터에서만 접속(`address = "localhost"`), 개발 메뉴 숨김. 밝은/어두운 화면은 시스템 설정을 따른다.
- 화면 디자인은 impeccable 스킬 지침(작업용 화면: 차분한 색, 익숙한 구성)을 따른다. 그 스킬의 보조 실행 파일은 쓰지 않는다.
- 앱 코드를 고친 뒤에는 앱을 껐다 켜야 반영되는 경우가 있다.

## 모의 투자 (src/autotrade/paper, crypto_live 모의 투자 탭)

- 지금 도는 것: 단타 #17(정식)·#18(비용을 넘는 틈만 자주), 느린 전략 #12·#13·#14, 대조군(비트코인 보유, ETF 4개·5개 나눠 담기, KODEX 200).
  #15(DART 공시)는 백테스트에서 탈락(모의로 안 넘김). #16(LLM 뉴스)은 2026-11-16 모의 기록 시작
  (`paper/news.py`, 서버 `.env`의 `CLAUDE_CODE_OAUTH_TOKEN` 필요, 준비는 `docs/server_setup.md`).
- **규칙·숫자는 `docs/experiments.md`에 등록한 것과 같아야 한다.** `scalp.py`의 `FORMAL`/`ALWAYS`, `slow.py`의 기준값을 바꾸면
  새 시도로 기록해야 한다.
- #21(코인 171개 단타)은 시작 전에 취소했다: `scalp.EXTREME_ON=False`. 코드·코인 목록(`scalp_universe.txt`)은 남아 있고, #17·#18 은 `ScalpParams.coins`(기본 4코인)로 코인을 묶어 둔다.
- 단타 체결은 신호를 본 호가창이 아니라 **0.2초 뒤 새로 받은 호가창**으로 계산한다(`Scalper.on_book`). 이 순서를 바꾸지 말 것.
- 느린 전략은 저장한 상태 없이 시작일 이후 시세로 매번 다시 계산한다(`slow.replay` → `run_backtest`). 아직 안 끝난 오늘 봉은
  마지막 줄로 들어가고, 엔진이 마지막 줄에서는 결정하지 않으므로 평가에만 쓰인다.
- 해외 가격 출처는 **OKX 선물**이 기준(2026-11-15 시작 전 바이낸스에서 변경). OKX 가 안 되면 바이낸스로 대신하고 `source`에 남긴다.
  **정식 판정은 `okx_swap` 기록만** 쓴다. #13 펀딩비도 OKX(받은 값은 `paper.db`의 funding 표에 쌓아 둠 — OKX 는 3개월치만 줌).
- 단타 채점은 **사건 단위**(`store.group_events`, 60초 안 신호는 묶음). 느린 전략 결정은 `paper.db`의 decisions 표에 내린 순간 값으로
  저장하고, 판정은 저장한 결정 기준(다시 계산한 값과 다르면 화면에 표시).
- 기록은 `data/paper/`(Git 제외): `paper.db`(거래·관측·호가 비용·켜져 있던 시간), `live.json`, `slow.json`. 따로 백업할 것.
  시험할 때는 환경 변수 `PAPER_DATA_DIR`로 다른 폴더를 쓴다(진짜 기록에 섞지 않기).
- 무료 서버 배포: `deploy/install.sh`(우분투, systemd + Cloudflare 무료 터널), 안내 `docs/server_setup.md`.
  서버는 `server.py --public --engine`: 127.0.0.1 에서만 듣고 **모든 접속에 접속 코드**를 묻는다(터널 뒤라 '이 컴퓨터면 통과'를 끔).
  임시 화면용 허깅페이스 Spaces: `deploy/hf/Dockerfile`(`--host=0.0.0.0`, 코드는 Secret `CRYPTO_LIVE_PIN`). 재시작하면 기록이 지워지므로 정식 기록용은 아님.
- 화면(`crypto_live/static/paper.*`)은 그리기만 한다. 계산을 화면 쪽에 넣지 말 것.
- **실전 주문 기능과 주문용 API 키는 붙이지 않는다**(전부 모의). `.claude/settings.json`이 `.env`·토큰·PIN 파일 읽기를 막는다.

## 반드시 지킬 규칙

1. **미래 참조 금지.** 전략은 엔진이 넘겨주는 `history`(결정일 장 마감까지의 데이터)만 사용한다. 전략 안에서 데이터를 따로 불러오거나 전역 데이터에 접근하지 않는다.
2. **체결은 결정 다음 거래일 시가.** 종가를 보고 같은 날 종가에 체결하는 코드를 만들지 않는다.
3. **비용은 항상 포함.** 성과를 보고할 때 `CostModel.zero()`를 쓰지 않는다(테스트 제외). 세율·수수료는 `config.py` 한 곳에서만 관리한다.
4. **벤치마크와 비교.** 전략 성과는 항상 같은 기간 지수 ETF 보유(Buy & Hold)와 나란히 보고한다.
5. **결과를 보고 파라미터를 고치지 않는다.** 전략 파라미터는 백테스트 전에 정하고, 바꿀 경우 그 사실과 이유를 `docs/experiments.md`에 기록한다(시도 횟수도 기록).
6. **엔진을 수정하면 `pytest`가 통과해야 한다.** 엔진 동작을 바꾸는 변경에는 테스트를 추가한다.
7. **비밀 정보는 `.env`에만.** API 키, 계좌번호, 시크릿을 코드·로그·커밋에 넣지 않는다. `.env`는 Git에 올리지 않는다.

## 알려진 한계

- FinanceDataReader 데이터: 개별 주식은 액면분할은 보정돼 있지만 **배당은 빠져 있다**(배당락일에 가격이 그만큼 떨어짐).
  국내 ETF는 분배금까지 반영된 가격이다. 개별 주식 전략을 ETF와 비교하면 주식 쪽이 불리하게 나온다.
- 거래정지일에는 직전 종가가 그대로 채워져 들어온다 → 개별 주식은 `load_prices(with_volume=True, drop_no_trade=True)`로 불러올 것.
- 현재 상장 종목만으로 종목군을 고르면 **생존 편향**이 생긴다 → `autotrade.universe.kospi_universe()`로 상장폐지 종목까지 포함하고,
  `run_backtest(delisted=...)`로 상장폐지를 처리할 것.
- 증권거래세는 연도·시장별로 다르다. `config.py`의 기본값은 가정치이므로 실제 세율을 확인해 수정할 것.

## 1분 데이터 모으기

- `scripts/collect_minute.py`가 야후 파이낸스에서 최근 7일 1분 데이터를 받아 `data/minute/종목코드.csv`에 이어 붙인다.
  실행 기록은 `data/minute/collect_log.txt`.
- 윈도우 작업 스케줄러 작업 **"auto-trading minute collect"**가 매일 18:00에 실행한다
  (컴퓨터가 꺼져 있었으면 켜진 뒤 실행). 7일 넘게 안 돌면 그 사이 데이터는 영영 빠진다.
- 모으는 종목은 스크립트의 `TICKERS`에서 바꾼다.
- 한계: 15:20~15:30 종가 결정 시간이 빠져 있고, 09:00 첫 봉은 거래량이 0으로 들어온다.
- `data/minute/`는 Git에 올리지 않는다(용량, 거래소 데이터 재배포 제한). 컴퓨터에만 있으므로 따로 백업할 것.

### 한국투자증권 API (과거 1년 치)

- `src/autotrade/kis.py`: 시세 조회 전용 클라이언트(주문 기능 없음). 키는 `.env`의 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_PAPER`.
- 접근 토큰은 `.kis_token.json`에 저장해 재사용한다(새로 받을 때마다 알림톡이 가므로). 이 파일도 Git 제외.
- `scripts/backfill_kis_minute.py`: 최근 약 1년 치 1분 데이터를 `data/minute_kis/`에 저장. 받은 날은 건너뛰므로 다시 실행하면 이어 받음.
- 야후 데이터와 시각 표기 방식이 다를 수 있어 **따로 저장**한다. 섞기 전에 같은 날 두 데이터를 비교해 확인할 것.

## 명령어

```bash
.venv/Scripts/python -m pytest          # 테스트
.venv/Scripts/python scripts/run_example.py   # 예제 백테스트(인터넷 필요)
```
