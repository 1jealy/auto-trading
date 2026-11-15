"""#15 공시 읽기 테스트 (인터넷 없이, 실제 공시 모양을 흉내 낸 표로)."""

from autotrade.dart import classify, number, parse_A, parse_B, parse_C


def html(rows: list[list[str]]) -> str:
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<html><style>td {{x}}</style><body><table>{body}</table></body></html>"


def test_classify():
    assert classify("연결재무제표기준영업(잠정)실적(공정공시)") == "A"
    assert classify("영업(잠정)실적(공정공시)") == "A"
    assert classify("연결재무제표기준영업(잠정)실적(공정공시)(2020.4분기)") == "A"
    assert classify("영업(잠정)실적(공정공시)(자회사의 주요경영사항)") is None
    assert classify("연결재무제표기준영업(잠정)실적(공정공시)(월별)") is None
    assert classify("[기재정정]영업(잠정)실적(공정공시)") is None
    assert classify("단일판매ㆍ공급계약체결") == "B"
    assert classify("단일판매ㆍ공급계약체결(자율공시)") == "B"
    assert classify("단일판매ㆍ공급계약해지") is None
    assert classify("단일판매ㆍ공급계약체결(자회사의 주요경영사항)") is None
    assert classify("주식소각결정") == "C"
    assert classify("[기재정정]주식소각결정") is None
    assert classify("영업실적등에대한전망(공정공시)") is None


def test_number():
    assert number("1,234") == 1234
    assert number("(1,234)") == -1234
    assert number("△1,234") == -1234
    assert number("-1,234") == -1234
    assert number("12.5%") == 12.5
    assert number("-") is None
    assert number("") is None
    assert number("흑자전환") is None


A_HEAD = [
    ["1. 연결실적내용", "단위 : 백만원, %"],
    ["구분", "당기실적", "전기실적", "전기대비증감율(%)", "전년동기실적", "전년동기대비증감율(%)"],
    ["(2020년 4분기)", "(2020년 3분기)", "(2019년 4분기)"],
    ["매출액", "당해실적", "69,247", "53,380", "29.7", "57,633", "20.2"],
    ["누계실적", "237,310", "168,063", "-", "255,696", "-7.2"],
]


def test_parse_A_growth_and_turnaround():
    p = parse_A(html(A_HEAD + [["영업이익", "당해실적", "20,906", "20,736", "0.8", "7,214", "189.8"]]))
    assert p.ok and p.signal
    assert p.values == {"영업이익_당기": 20906, "영업이익_전년동기": 7214}
    # 29% 증가 → 신호 아님, 30% 정확히 → 신호
    assert not parse_A(html(A_HEAD + [["영업이익", "당해실적", "129", "1", "-", "100", "29"]])).signal
    assert parse_A(html(A_HEAD + [["영업이익", "당해실적", "130", "1", "-", "100", "30"]])).signal
    # 적자 → 흑자
    assert parse_A(html(A_HEAD + [["영업이익", "당해실적", "5", "1", "-", "(3)", "흑전"]])).signal
    # 이번에 적자면 신호 아님
    assert not parse_A(html(A_HEAD + [["영업이익", "당해실적", "-5", "1", "-", "-30", "-"]])).signal


def test_parse_A_failures_are_reported():
    p = parse_A(html(A_HEAD + [["영업이익", "당해실적", "20", "10", "-", "-", "-"]]))
    assert not p.ok and "전년동기" in p.reason
    assert not parse_A(html([["아무", "표"]])).ok


def test_parse_A_does_not_read_cumulative_row():
    rows = A_HEAD + [
        ["영업이익", "당해실적", "100", "1", "-", "100", "0"],
        ["누계실적", "900", "1", "-", "100", "800"],
    ]
    p = parse_A(html(rows))
    assert p.ok and not p.signal


def test_parse_B():
    rows = [
        ["1. 판매ㆍ공급계약 내용", "의료기기 공급"],
        ["2. 계약내역", "조건부 계약여부", "미해당"],
        ["계약금액 총액(원)", "262,401,000"],
        ["최근 매출액(원)", "35,811,000"],
        ["매출액 대비(%)", "732.74"],
        ["3. 계약상대방", "-"],
        ["-최근 매출액(원)", "-"],
    ]
    p = parse_B(html(rows))
    assert p.ok and p.signal and p.values["매출액대비%"] == 732.74
    rows[4] = ["매출액 대비(%)", "19.99"]
    assert not parse_B(html(rows)).signal
    rows[4] = ["매출액 대비(%)", "-"]  # 비율 칸이 비면 금액으로 계산
    p = parse_B(html(rows))
    assert p.ok and p.values["매출액대비%(계산)"] == 732.74


def test_parse_C():
    rows = [
        ["1. 소각할 주식의 종류와 수", "보통주식 (주)", "1,000,000"],
        ["종류주식 (주)", "-"],
        ["2. 발행주식 총수", "보통주식 (주)", "50,000,000"],
        ["종류주식 (주)", "1,000"],
        ["3. 1주당 가액(원)", "500"],
    ]
    p = parse_C(html(rows))
    assert p.ok and p.signal and p.values["비율%"] == 2.0
    rows[0] = ["1. 소각할 주식의 종류와 수", "보통주식 (주)", "400,000"]
    assert not parse_C(html(rows)).signal
