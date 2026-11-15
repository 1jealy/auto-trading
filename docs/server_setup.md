# 무료 서버에 모의 투자 엔진 올리기

컴퓨터를 켜 두지 않아도 모의 투자(#12~#18)가 계속 기록되도록, 무료 클라우드 서버에 올리는 방법입니다.
엔진은 **공개 시세만 읽고 주문은 내지 않으므로** 키나 비밀 정보가 필요 없습니다.

## 어디에 올리나: 오라클 클라우드 "항상 무료" (서울·춘천)

| 후보 | 계속 켜 둘 수 있나 | 바이낸스 선물 | 비고 |
|---|---|---|---|
| **오라클 클라우드 항상 무료 (서울/춘천)** | ✅ 기한 없음 | ✅ 한국이라 연결됨 | 추천. 가입 때 카드 본인 확인(돈은 안 나감) |
| 구글 클라우드 e2-micro 무료 | ✅ | ❌ 미국만 무료라 막힘 | #17 정식 채점·#13 계산 불가 (OKX로 대신) |
| Render·Railway 등 무료 | ❌ 안 쓰면 잠듦 | ❌ | 엔진이 멈춤 |

- 오라클은 가입할 때 고른 **홈 지역에만** 무료 서버를 만들 수 있고, 나중에 바꿀 수 없습니다. 꼭 **South Korea Central (Seoul)** 또는
  **South Korea North (Chuncheon)**을 고르세요.
- 무료 서버 모양은 **VM.Standard.E2.1.Micro**(메모리 1GB)면 충분합니다. ARM(A1)은 서울에서 자리가 없을 때가 많습니다.

## 1. 서버 만들기 (오라클 화면에서, 10분)

1. https://www.oracle.com/kr/cloud/free/ 에서 가입 (홈 지역: 서울 또는 춘천).
2. 메뉴 → **Compute → Instances → Create instance**.
3. Image: **Ubuntu 24.04** (또는 22.04), Shape: **VM.Standard.E2.1.Micro** (Always Free 표시 확인).
4. **SSH 키**: "Generate a key pair for me" → **개인 키 내려받기**(잃어버리면 접속 못 함).
5. Create → 1~2분 뒤 **Public IP address**를 적어 둡니다.

방화벽(포트)을 열 필요는 없습니다. 화면은 서버 안에서만 열리고, 밖에서는 Cloudflare 터널(HTTPS)로 들어옵니다.

## 2. 접속해서 한 줄 실행 (5~10분)

윈도우 PowerShell(또는 맥 터미널)에서:

```bash
ssh -i 내려받은키파일.key ubuntu@서버IP
```

접속되면:

```bash
curl -fsSL https://raw.githubusercontent.com/1jealy/auto-trading/main/deploy/install.sh | bash
```

끝나면 이렇게 나옵니다:

```
  접속 주소  : https://아무-단어-조합.trycloudflare.com/paper.html
  접속 코드  : 12자리 숫자
```

폰·패드·어느 컴퓨터에서든 그 주소를 열고 코드를 넣으면 됩니다(30일 동안 기억). 홈 화면에 추가하면 앱처럼 열립니다.

## 3. 그다음

| 하고 싶은 것 | 명령 (서버에 ssh 로 접속해서) |
|---|---|
| 주소·코드·엔진 상태 보기 | `bash ~/auto-trading/deploy/status.sh` |
| 최신 코드로 바꾸기 | `bash ~/auto-trading/deploy/install.sh` (기록은 그대로) |
| 엔진 기록 자세히 보기 | `sudo journalctl -u coin-monitor -f` |
| 잠깐 끄기 / 다시 켜기 | `sudo systemctl stop coin-monitor` / `sudo systemctl start coin-monitor` |
| 기록 백업 (내 컴퓨터에서) | `scp -i 키파일 -r ubuntu@서버IP:auto-trading/data/paper ./paper-backup` |

- **주소는 터널이 다시 시작될 때(서버 재부팅 등) 바뀝니다.** 그때 `status.sh`로 새 주소를 확인하세요.
  주소를 고정하려면 Cloudflare 계정과 도메인이 필요합니다(나중에 필요하면 설정).
- 접속 코드를 바꾸려면 `rm ~/auto-trading/crypto_live/.pin && sudo systemctl restart coin-monitor` 뒤 `status.sh`.
- 기록(`data/paper/`)은 그 서버에만 있습니다. 가끔 백업하세요.

## 보안

- 화면 서버는 `--public` 모드로 돌아갑니다: 서버 안(127.0.0.1)에서만 듣고, **모든 접속에 12자리 접속 코드**를 묻고,
  8번 틀리면 5분 잠그며, 로그인 쿠키는 HTTPS 에서만 보냅니다.
- 보이는 것은 가짜 돈 모의 투자 기록과 공개 시세뿐이고, 서버에는 거래소 키가 없습니다.
- **실제 계좌를 연결할 때는 이 방식을 쓰지 않습니다**(README의 원칙 그대로).

## 잘 안 될 때

- 화면 서버가 `active`가 아니면: `sudo journalctl -u coin-monitor -n 50`로 오류를 보세요.
- 엔진 상태 카드에 해외 가격이 "OKX 선물"로 나오면 바이낸스 선물 연결이 안 되는 것입니다. 서버 지역이 한국인지 확인하세요.
- 오라클이 "Out of capacity"라고 하면 잠시 뒤 다시 시도하거나 E2.1.Micro를 고르세요.

---

## 임시: 허깅페이스 Spaces 로 화면만 띄우기 (카드·SSH 필요 없음)

**화면을 보는 용도**입니다. 정식 기록용이 아닙니다.

- 미국 서버라 바이낸스 선물이 막혀 **OKX 가격으로 시험 기록**만 쌓입니다(#17 채점 제외, #13 계산 불가).
- 무료 공간은 **다시 시작하면 기록이 지워지고**, **48시간 아무도 안 열면 잠듭니다**(열면 다시 깨어남, 기록은 처음부터).

### 만드는 순서 (웹 화면에서 5분)

1. https://huggingface.co 가입 → 오른쪽 위 프로필 → **New Space**.
2. Space name: 예) `coin-monitor` · SDK: **Docker** → **Blank** · Hardware: **CPU basic (free)** · **Public** → Create Space.
3. Space 의 **Settings → Variables and secrets → New secret**
   - Name: `CRYPTO_LIVE_PIN` · Value: **직접 정한 숫자 12자리 이상** (이게 접속 코드. 남에게 알려 주지 말 것)
4. **Files → + Add file → Create a new file** → 파일 이름 `Dockerfile` →
   깃허브의 [`deploy/hf/Dockerfile`](https://github.com/1jealy/auto-trading/blob/main/deploy/hf/Dockerfile) 내용을 그대로 붙여 넣고 **Commit**.
5. 몇 분 뒤 상태가 **Running** 이 되면, Space 오른쪽 위 **⋮ → Embed this Space** 에 나오는 **Direct URL**
   (`https://아이디-coin-monitor.hf.space`) 뒤에 `/paper.html` 을 붙여 엽니다.
   - Space 페이지(huggingface.co/spaces/…) 안에 끼워진 화면에서는 로그인이 유지되지 않습니다. 꼭 Direct URL 로 여세요.
6. 접속 코드(3번에서 정한 숫자)를 넣으면 끝. 폰에서는 홈 화면에 추가해 두면 편합니다.

- 코드를 고친 뒤 최신으로 바꾸려면: Space **Settings → Restart** (기록은 지워짐).
- 오류가 나면 Space 의 **Logs** 탭 내용을 보면 됩니다.
