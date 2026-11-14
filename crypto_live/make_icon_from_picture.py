"""그림 한 장으로 홈 화면 아이콘 만들기 (밝은 배경 → 어두운 배경).

    python crypto_live/make_icon_from_picture.py [그림 경로]   # 기본: crypto_live/icon-source.jpg

- 그림 가장자리(위·왼쪽·오른쪽)에서 이어진 밝은 흰 배경만 골라 어두운 배경으로 바꾼다.
  인물 안쪽의 흰 부분(옷, 피부)은 테두리 선으로 막혀 있어서 그대로 남는다.
- 아래쪽 가장자리는 시작점에서 뺀다(옷이 아래 끝에 닿아 있어서).
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
OUT = HERE / "static" / "icons"
FACE_BOX = (0.30, 0.30, 0.80, 0.95)  # 얼굴·목·옷이 있는 곳 (비율) — 여기 흰 부분은 지우지 않음


def background_mask(im: Image.Image, light: int = 228, chroma: int = 22) -> Image.Image:
    """밝고 색이 거의 없는 픽셀 중 위·왼쪽·오른쪽 가장자리와 이어진 것 = 배경(255)."""
    w, h = im.size
    px = im.load()

    def is_bg(x, y):
        r, g, b = px[x, y]
        return min(r, g, b) >= light and max(r, g, b) - min(r, g, b) <= chroma

    seen = bytearray(w * h)
    q = deque()
    seeds = [(x, 0) for x in range(w)] + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    for x, y in seeds:
        if is_bg(x, y) and not seen[y * w + x]:
            seen[y * w + x] = 1
            q.append((x, y))
    while q:
        x, y = q.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and is_bg(nx, ny):
                seen[ny * w + nx] = 1
                q.append((nx, ny))
    # 머리카락 틈에 갇힌 작은 흰 조각도 배경으로 (얼굴 영역은 제외: 눈·피부 하이라이트 보호)
    fx0, fy0, fx1, fy1 = FACE_BOX
    comp = bytearray(w * h)
    for sy in range(h):
        for sx in range(w):
            i = sy * w + sx
            if seen[i] or comp[i] or not is_bg(sx, sy):
                continue
            pts, q2 = [], deque([(sx, sy)])
            comp[i] = 1
            while q2:
                x, y = q2.popleft()
                pts.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    j = ny * w + nx
                    if 0 <= nx < w and 0 <= ny < h and not comp[j] and not seen[j] and is_bg(nx, ny):
                        comp[j] = 1
                        q2.append((nx, ny))
            cx = sum(p[0] for p in pts) / len(pts) / w
            cy = sum(p[1] for p in pts) / len(pts) / h
            in_face = fx0 <= cx <= fx1 and fy0 <= cy <= fy1
            if len(pts) <= w * h * 0.006 and not in_face and cy < 0.8:
                for x, y in pts:
                    seen[y * w + x] = 1

    mask = Image.frombytes("L", (w, h), bytes(255 if v else 0 for v in seen))
    return mask.filter(ImageFilter.GaussianBlur(0.7))


def fringe_weight(im: Image.Image, mask: Image.Image) -> Image.Image:
    """배경 근처(2~3px)에 있는 '밝은' 픽셀일수록 배경 쪽으로 섞는 정도(0~255).
    잘라내지 않고 부드럽게 어둡게 해서 흰 테두리만 없애고, 어두운 머리카락은 그대로 둔다."""
    near = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
    light = im.convert("L").point(lambda v: 0 if v < 170 else min(255, int((v - 170) / 70 * 255)))
    return ImageChops.multiply(near, light)


def dark_backdrop(size: int) -> Image.Image:
    """가운데가 조금 밝은 어두운 바탕 + 리본 색을 닮은 은은한 붉은 빛."""
    bg = Image.new("RGB", (size, size), (11, 11, 14))
    glow = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(glow)
    for cx, cy, r, col in [(0.5, 0.42, 0.42, (38, 34, 46)), (0.18, 0.22, 0.22, (70, 14, 22))]:
        d.ellipse([(cx - r) * size, (cy - r) * size, (cx + r) * size, (cy + r) * size], fill=col)
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))
    return ImageChops.add(bg, glow)


def build(src: Path, size: int, safe: float = 1.0) -> Image.Image:
    pic = Image.open(src).convert("RGB")
    side = min(pic.size)
    pic = pic.crop(((pic.width - side) // 2, (pic.height - side) // 2,
                    (pic.width + side) // 2, (pic.height + side) // 2))
    mask = ImageChops.lighter(background_mask(pic), fringe_weight(pic, background_mask(pic)))
    big = 1024
    pic_big = pic.resize((big, big), Image.LANCZOS)
    keep = Image.eval(mask, lambda v: 255 - v).resize((big, big), Image.LANCZOS)
    canvas = dark_backdrop(big)
    if safe < 1:  # 둥근 아이콘(안드로이드)용: 그림을 가운데 안전 영역으로 줄임
        inner = int(big * safe)
        pic_big, keep = pic_big.resize((inner, inner), Image.LANCZOS), keep.resize((inner, inner), Image.LANCZOS)
        off = (big - inner) // 2
        canvas.paste(pic_big, (off, off), keep)
    else:
        canvas.paste(pic_big, (0, 0), keep)
    return canvas.resize((size, size), Image.LANCZOS)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "icon-source.jpg"
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in [("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180), ("icon-32.png", 32)]:
        build(src, size).save(OUT / name, optimize=True)
    build(src, 512, safe=0.82).save(OUT / "icon-maskable-512.png", optimize=True)
    build(src, 1024).save(HERE / "icon-preview.png")
    print("만들었습니다:", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
