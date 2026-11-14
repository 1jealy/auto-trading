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

## 코인 시세 분석 앱 (실시간)

프로젝트 폴더의 **`코인시세.bat`을 더블클릭**하면 브라우저에 열립니다(http://127.0.0.1:8765, 이 컴퓨터에서만 접속 가능). 검은 창을 닫으면 앱도 꺼집니다.

- 업비트 실시간 체결로 캔들 차트가 계속 움직입니다(1분·5분·15분·1시간·1일). 20개·60개 평균선, 실험 #5의 새벽 6~8시 구간 표시.
- **지금 사서 바로 팔면** — 업비트·코인원의 실시간 가격 차이와 수수료를 더한 왕복 비용.
- 업비트 호가 10단계, 최근 체결, 최근 캔들 표.
- 파이썬 표준 라이브러리만 씁니다. 서버는 정해 둔 공개 시세 주소만 대신 불러 주고, 계좌·주문 기능은 없습니다.

직접 실행하려면:

```bash
.venv/Scripts/python crypto_live/server.py 8765
```

## 새 전략 추가하기

1. `docs/experiments.md`에 가설(왜 수익이 나야 하는지)과 규칙, 파라미터를 먼저 적습니다.
2. `src/autotrade/strategies/`에 `Strategy`를 상속한 클래스를 만들고 `target_weights(history)`를 구현합니다.
3. 벤치마크(Buy & Hold)와 함께 백테스트하고 결과를 `docs/experiments.md`에 기록합니다.

규칙은 [CLAUDE.md](CLAUDE.md)를 참고하세요.
