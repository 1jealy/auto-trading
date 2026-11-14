# auto-trading

개인용 주식 자동매매 연구 프로젝트입니다. 지금은 전략을 **검증하는 백테스트 단계**입니다.

## 설치

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```

## 사용

```bash
.venv/Scripts/python -m pytest
.venv/Scripts/python scripts/run_example.py
```

## 결과 보기 앱

프로젝트 폴더의 **`실행.bat`을 더블클릭**하면 브라우저에 화면이 열립니다(이 컴퓨터에서만 접속 가능).

- **실험 결과** — 실험 #1~#4를 기록된 규칙 그대로 계산한 그래프·표와 실험 기록 원문. 검증 기간은 이미 돌린 실험(#1)만 열려 있습니다.
- **숫자 바꿔 보기** — 연습 기간에서 규칙의 숫자를 바꿔 기록된 규칙과 비교. 여기서 본 결과는 기록되지 않습니다.
- **데이터 보기** — 하루 단위 가격, 1분 데이터, 1분 데이터 모으기 상태.

직접 실행하려면:

```bash
.venv/Scripts/python -m streamlit run app/app.py
```

## 코인 모니터 (실시간 시세 + 모의 계좌)

화면은 두 개입니다.

- **내 계좌** — 총 자산, 손익, 비트코인 보유량·평균 산 가격, 계좌 가치 그래프, 알림 조건(가격 상한·하한, 손익률), 실험 #5 시간대 표시, 거래 내역.
  지금은 **모의 계좌**입니다(`crypto_live/static/mock_account.json`의 예시 기록 + 실제 실시간 가격). 주문 기능은 없습니다.
- **시세** — 업비트 실시간 캔들 차트(1분~1일), 20·60개 평균선, 새벽 6~8시 구간, 업비트·코인원 왕복 비용 비교, 호가, 최근 체결.

### 여는 방법

| 방법 | 실행 | 어디서 | 비고 |
|---|---|---|---|
| 이 PC에서만 | `코인시세.bat` 더블클릭 | 이 PC 브라우저 | |
| 같은 와이파이의 폰·패드 | `코인모니터_폰연결.bat` 더블클릭 | 창에 나온 주소(http://192.168…:8765) | 창에 나온 **6자리 PIN** 필요. 처음엔 윈도우 방화벽 창에서 '개인 네트워크'만 허용 |
| 어디서나 (정적 호스팅) | `crypto_live/static` 폴더를 무료 정적 호스팅에 올림 | 인터넷 주소 | 업비트만 사용(코인원 비교 없음), 예시 모의 계좌만, PIN 없음 |

폰·패드에서 **홈 화면에 추가**하면 앱처럼 열립니다(안드로이드 크롬: 메뉴 → 홈 화면에 추가 / 아이패드 사파리: 공유 → 홈 화면에 추가).

- PC 서버로 열면 `crypto_live/account.json`을 만들어 두면 예시 대신 그 기록을 씁니다(Git에 안 올라감).
- 서버는 파이썬 표준 라이브러리만 쓰고, 정해 둔 공개 시세 주소만 대신 불러 줍니다.

```bash
.venv/Scripts/python crypto_live/server.py 8765          # 이 PC에서만
.venv/Scripts/python crypto_live/server.py 8765 --lan    # 같은 와이파이 (PIN)
```

## 새 전략 추가하기

1. `docs/experiments.md`에 가설(왜 수익이 나야 하는지)과 규칙, 파라미터를 먼저 적습니다.
2. `src/autotrade/strategies/`에 `Strategy`를 상속한 클래스를 만들고 `target_weights(history)`를 구현합니다.
3. 벤치마크(Buy & Hold)와 함께 백테스트하고 결과를 `docs/experiments.md`에 기록합니다.

규칙은 [CLAUDE.md](CLAUDE.md)를 참고하세요.
