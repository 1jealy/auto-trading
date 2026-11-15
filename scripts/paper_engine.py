"""모의 투자 엔진만 따로 실행 (화면 없이 기록만). 규칙: docs/experiments.md #12~#18, 설명: docs/plan.md

    python scripts/paper_engine.py           # 끄려면 Ctrl+C
    python scripts/paper_engine.py --force   # "이미 돌고 있음" 검사를 건너뜀

공개 시세만 읽고 주문은 내지 않는다. 화면과 함께 켜려면 코인시세.bat (server.py --engine).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autotrade.paper.run import main  # noqa: E402

if __name__ == "__main__":
    main()
