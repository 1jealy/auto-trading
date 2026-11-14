# 클라우드 환경 설정

PC를 꺼도 폰·패드에서 이 프로젝트를 이어서 작업하기 위한 Claude Code 클라우드 환경 설정입니다.
클라우드 세션은 **GitHub 에 올린 코드**에서 시작합니다. 받아 둔 시세 데이터(`data/`), `.env`(키), `.venv` 는 넘어가지 않습니다.

## 0. 먼저 필요한 것 (조직 계정)

조직 소유자가 아래를 켜 줘야 합니다(2026-11-14 요청함).

- 조직 설정 → 커넥터 → **GitHub 커넥터 켜기**: https://claude.ai/admin-settings/connectors
- 조직 설정 → Claude Code → (선택) **Quick setup**, **Remote Control**: https://claude.ai/admin-settings/claude-code

## 1. GitHub 연결

claude.ai/code (또는 Claude 앱 Code 탭) → **Sign in with GitHub** → Claude GitHub 앱을 개인 계정 `1jealy`에 설치하고
저장소 **`1jealy/auto-trading`** 선택 (비공개 저장소라 꼭 필요).

## 2. 클라우드 환경 만들기

Code 탭 입력창 아래 **환경 선택 메뉴 → 새 환경** (또는 claude.ai/code 의 환경 설정).

| 항목 | 값 |
|---|---|
| 이름 | `auto-trading` |
| 네트워크 접근 | **Limited** + "Allow package managers" 체크 유지 |
| 허용 도메인 (Allowed domains) | 아래 목록을 한 줄에 하나씩 |
| 설정 스크립트 | `scripts/cloud_setup.sh` 내용을 그대로 붙여 넣기 |
| 환경 변수 | 지금은 없음 |
| 네트워크 시크릿 | 지금은 없음 (한국투자증권 키를 쓸 때만, 아래 참고) |

### 허용 도메인

```
api.upbit.com
api.bithumb.com
api.coinone.co.kr
data.krx.co.kr
kind.krx.co.kr
fchart.stock.naver.com
api.finance.naver.com
m.stock.naver.com
query1.finance.yahoo.com
query2.finance.yahoo.com
fc.yahoo.com
cdn.jsdelivr.net
unpkg.com
```

- 위 세 줄(업비트·빗썸·코인원)은 코인 실험·앱에 꼭 필요합니다.
- KRX·네이버·야후는 주식 데이터(FinanceDataReader, yfinance)용입니다. 라이브러리가 쓰는 주소가 바뀔 수 있어서,
  막히면 오류 메시지에 나온 주소를 추가하세요.
- jsdelivr·unpkg 는 앱 화면(글꼴, 차트)을 클라우드에서 확인할 때만 필요합니다.

### 한국투자증권 키를 쓰게 되면

키는 **환경 변수가 아니라 네트워크 시크릿**으로 등록합니다(Claude 도 값을 볼 수 없음).
호스트는 `openapi.koreainvestment.com`(실전) 또는 `openapivts.koreainvestment.com`(모의)만 지정합니다.

## 3. 클라우드 세션에서 처음 할 일

```bash
bash scripts/cloud_data.sh crypto   # 코인 1분 데이터 다시 받기 (오래 걸림, 백그라운드 권장)
python3 -m pytest -q                # 테스트
```

- 오래 걸리는 다운로드는 세션이 쉬는 동안 멈출 수 있습니다. 다시 실행하면 이어서 받습니다.
- 매일 자동으로 할 일(1분 데이터 모으기 등)은 클라우드 **예약 작업(Routines)**이 더 알맞습니다.

## 4. PC 전용으로 남는 것

- 윈도우 작업 스케줄러의 1분 데이터 수집(`scripts/collect_minute.py`)
- 와이파이·PIN 서버 (`코인모니터_폰연결.bat`)
- 이미 받은 데이터와 `.env`
