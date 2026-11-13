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

## 반드시 지킬 규칙

1. **미래 참조 금지.** 전략은 엔진이 넘겨주는 `history`(결정일 장 마감까지의 데이터)만 사용한다. 전략 안에서 데이터를 따로 불러오거나 전역 데이터에 접근하지 않는다.
2. **체결은 결정 다음 거래일 시가.** 종가를 보고 같은 날 종가에 체결하는 코드를 만들지 않는다.
3. **비용은 항상 포함.** 성과를 보고할 때 `CostModel.zero()`를 쓰지 않는다(테스트 제외). 세율·수수료는 `config.py` 한 곳에서만 관리한다.
4. **벤치마크와 비교.** 전략 성과는 항상 같은 기간 지수 ETF 보유(Buy & Hold)와 나란히 보고한다.
5. **결과를 보고 파라미터를 고치지 않는다.** 전략 파라미터는 백테스트 전에 정하고, 바꿀 경우 그 사실과 이유를 `docs/experiments.md`에 기록한다(시도 횟수도 기록).
6. **엔진을 수정하면 `pytest`가 통과해야 한다.** 엔진 동작을 바꾸는 변경에는 테스트를 추가한다.
7. **비밀 정보는 `.env`에만.** API 키, 계좌번호, 시크릿을 코드·로그·커밋에 넣지 않는다. `.env`는 Git에 올리지 않는다.

## 알려진 한계

- FinanceDataReader의 국내 주가는 액면분할 등이 수정되지 않은 값일 수 있다. 분할 이벤트가 있는 종목은 결과를 의심할 것.
- 현재 상장 종목만으로 종목군을 고르면 **생존 편향**이 생긴다(상장폐지 종목 누락).
- 증권거래세는 연도·시장별로 다르다. `config.py`의 기본값은 가정치이므로 실제 세율을 확인해 수정할 것.

## 명령어

```bash
.venv/Scripts/python -m pytest          # 테스트
.venv/Scripts/python scripts/run_example.py   # 예제 백테스트(인터넷 필요)
```
