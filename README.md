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

## 새 전략 추가하기

1. `docs/experiments.md`에 가설(왜 수익이 나야 하는지)과 규칙, 파라미터를 먼저 적습니다.
2. `src/autotrade/strategies/`에 `Strategy`를 상속한 클래스를 만들고 `target_weights(history)`를 구현합니다.
3. 벤치마크(Buy & Hold)와 함께 백테스트하고 결과를 `docs/experiments.md`에 기록합니다.

규칙은 [CLAUDE.md](CLAUDE.md)를 참고하세요.
