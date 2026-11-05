"""유튜브 채널용 프로필 사진과 배너 이미지를 만든다.

블로그 사이트 아이콘(assets/brand/site-icon.png)과 같은 색·글꼴을 쓴다.
결과: assets/brand/youtube/profile.png(800x800), banner.png(2560x1440)

배너는 기기마다 잘려 보이는 범위가 달라서, 글자는 모든 기기에서 보이는
가운데 1546x423 영역 안에만 둔다(유튜브 안내 기준).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_BOLD = ROOT / "assets/fonts/NotoSansCJKkr-Bold.otf"
FONT_REG = ROOT / "assets/fonts/NotoSansCJKkr-Regular.otf"
OUT = ROOT / "assets/brand/youtube"

GREEN = (31, 111, 80)
GREEN_DARK = (22, 84, 60)
YELLOW = (255, 214, 90)
WHITE = (255, 255, 255)
SOFT = (214, 236, 226)


def draw_check(d: ImageDraw.ImageDraw, x: float, y: float, size: float, width: int) -> None:
    """(x, y)를 왼쪽 위로 하는 size 크기 칸에 체크 표시를 그린다."""
    pts = [(x, y + size * 0.55), (x + size * 0.38, y + size * 0.9), (x + size, y + size * 0.1)]
    d.line(pts, fill=YELLOW, width=width, joint="curve")
    r = width / 2
    for px, py in (pts[0], pts[-1]):
        d.ellipse([px - r, py - r, px + r, py + r], fill=YELLOW)


def centered_text(d, cx, cy, text, font, fill):
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text((cx - w / 2 - box[0], cy - h / 2 - box[1]), text, font=font, fill=fill)
    return w, h


def make_profile() -> Path:
    # 유튜브는 프로필을 동그랗게 잘라서 보여 주므로 꽉 찬 정사각형으로 만들고
    # 글자와 체크는 원 안쪽에 둔다.
    s = 800
    im = Image.new("RGB", (s, s), GREEN)
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONT_BOLD), 370)
    centered_text(d, s / 2 - 55, s / 2 - 20, "잘", font, WHITE)
    draw_check(d, 560, 530, 120, 34)
    path = OUT / "profile.png"
    im.save(path, optimize=True)
    return path


def make_banner() -> Path:
    w, h = 2560, 1440
    im = Image.new("RGB", (w, h), GREEN)
    d = ImageDraw.Draw(im)
    # 가장자리(TV에서만 보이는 부분)는 조금 어둡게 해 가운데로 눈이 가게 한다.
    d.rectangle([0, 0, w, h], fill=GREEN_DARK)
    d.rectangle([0, 360, w, h - 360], fill=GREEN)

    cx, cy = w / 2, h / 2
    title = ImageFont.truetype(str(FONT_BOLD), 150)
    sub = ImageFont.truetype(str(FONT_REG), 50)
    url = ImageFont.truetype(str(FONT_BOLD), 44)

    # 제목 "잘체크" + 오른쪽 체크 표시를 한 덩어리로 가운데 정렬
    box = d.textbbox((0, 0), "잘체크", font=title)
    tw = box[2] - box[0]
    check = 110
    gap = 30
    left = cx - (tw + gap + check) / 2
    ty = cy - 120
    d.text((left - box[0], ty - (box[3] - box[1]) / 2 - box[1]), "잘체크", font=title, fill=WHITE)
    draw_check(d, left + tw + gap, ty - check / 2 + 10, check, 26)

    centered_text(d, cx, cy + 45, "지원금·세금·생활 제도, 공식 자료로 확인해 짧게 정리합니다", sub, SOFT)
    centered_text(d, cx, cy + 140, "jalcheck.com", url, YELLOW)

    path = OUT / "banner.png"
    im.save(path, optimize=True)
    return path


def make_safe_preview(banner: Path) -> Path:
    """모든 기기에서 보이는 가운데 영역에 빨간 선을 그은 확인용 사본."""
    im = Image.open(banner).convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    sw, sh = 1546, 423
    d.rectangle([(w - sw) / 2, (h - sh) / 2, (w + sw) / 2, (h + sh) / 2], outline=(255, 0, 0), width=6)
    path = OUT / "banner-safe-area-check.png"
    im.save(path)
    return path


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    p = make_profile()
    b = make_banner()
    c = make_safe_preview(b)
    for f in (p, b, c):
        print(f, f.stat().st_size // 1024, "KB")
