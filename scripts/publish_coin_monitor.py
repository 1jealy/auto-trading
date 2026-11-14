"""코인 모니터 화면을 공개 저장소(1jealy/coin-monitor, GitHub Pages)로 내보낸다.

    python scripts/publish_coin_monitor.py          # 복사 + 커밋 + 올리기
    python scripts/publish_coin_monitor.py --dry    # 복사만 (올리지 않음)

- 연구 저장소(auto-trading)는 비공개로 두고, 화면 파일만 옆 폴더 ../coin-monitor 로 복사한다.
- 올라가는 것: 화면(HTML·JS·CSS), 아이콘, 예시 모의 계좌. PC 서버 전용(PIN 화면, 서버 코드)은 빼고,
  개인 계좌 기록(account.json)·PIN·키는 애초에 static 폴더에 없다.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "crypto_live" / "static"
DEST = ROOT.parent / "coin-monitor"
SKIP = {"login.html"}  # PC 서버에서만 쓰는 화면
ALLOWED_SUFFIX = {".html", ".js", ".css", ".webmanifest", ".png", ".json"}

README = """# 코인 모니터

비트코인 시세와 **모의 계좌**를 실시간으로 보는 웹 앱입니다. 폰·패드에서 홈 화면에 추가하면 앱처럼 열립니다.

- 열기: https://1jealy.github.io/coin-monitor/account.html
- 가격: 업비트 공개 시세(실시간 웹소켓, REST)를 브라우저가 직접 받습니다. 서버는 없습니다.
- 계좌: `mock_account.json`의 **예시 기록**입니다. 실제 계좌가 아니며, 주문 기능도 없습니다.
- 투자 판단의 근거가 아니라 관찰용입니다.

사용한 공개 라이브러리: [Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache-2.0),
[Pretendard](https://github.com/orioncactus/pretendard) (SIL OFL 1.1).
"""


def run(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=DEST, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def main(dry: bool) -> None:
    if not (DEST / ".git").exists():
        raise SystemExit(f"{DEST} 가 git 저장소가 아닙니다. 먼저: gh repo clone 1jealy/coin-monitor \"{DEST}\"")

    # 이전 파일을 지우고(.git 제외) 새로 복사 → 지운 파일도 반영됨
    for p in DEST.iterdir():
        if p.name == ".git":
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    for f in SRC.rglob("*"):
        if f.is_dir() or f.name in SKIP:
            continue
        if f.suffix not in ALLOWED_SUFFIX:
            print(f"건너뜀(허용 안 된 파일 형식): {f.relative_to(SRC)}")
            continue
        out = DEST / f.relative_to(SRC)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
    # GitHub Pages 는 파일을 10분간 브라우저에 저장해 둔다 → 새 HTML 이 예전 JS 와 섞이지 않게 주소에 버전 표시
    stamp = time.strftime("%Y%m%d%H%M%S")
    asset = re.compile(r'((?:src|href)=")((?:styles\.css|net\.js|app\.js|account\.js|manifest\.webmanifest))(")')
    for html in DEST.glob("*.html"):
        text = html.read_text(encoding="utf-8")
        html.write_text(asset.sub(rf"\g<1>\g<2>?v={stamp}\g<3>", text), encoding="utf-8")
    (DEST / "README.md").write_text(README, encoding="utf-8")
    (DEST / ".nojekyll").write_text("", encoding="utf-8")  # GitHub Pages 가 파일을 그대로 쓰게

    files = sorted(str(p.relative_to(DEST)) for p in DEST.rglob("*") if p.is_file() and ".git" not in p.parts)
    print("공개될 파일:", *files, sep="\n  ")
    if dry:
        return
    run("git", "add", "-A")
    if not run("git", "status", "--porcelain").strip():
        print("바뀐 것이 없어 올리지 않았습니다.")
        return
    run("git", "commit", "-q", "-m", "Update coin monitor from auto-trading")
    run("git", "push", "-q")
    print("올렸습니다: https://1jealy.github.io/coin-monitor/account.html (반영까지 1~2분)")


if __name__ == "__main__":
    main("--dry" in sys.argv)
