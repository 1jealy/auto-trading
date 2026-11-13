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

## 새 전략 추가하기

1. `docs/experiments.md`에 가설(왜 수익이 나야 하는지)과 규칙, 파라미터를 먼저 적습니다.
2. `src/autotrade/strategies/`에 `Strategy`를 상속한 클래스를 만들고 `target_weights(history)`를 구현합니다.
3. 벤치마크(Buy & Hold)와 함께 백테스트하고 결과를 `docs/experiments.md`에 기록합니다.

규칙은 [CLAUDE.md](CLAUDE.md)를 참고하세요.
