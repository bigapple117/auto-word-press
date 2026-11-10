"""쇼츠 화면 구성(.ass): 위쪽 질문 상자, 사이트 이름, 진행 막대, 정보 카드·체크 목록, 숫자 강조 자막.

카드·효과음 시간도 여기서 정한다(video_renderer가 소리를 섞을 때 씀).
색은 사이트와 같은 청록(#0f6b74), 강조는 노랑, 주의는 빨강.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from src.tts_engine import Caption, _ass_escape, _ass_time

WATERMARK = "잘체크 · jalcheck.com"
# ASS 색은 &HBBGGRR&
TEAL, DARK, YELLOW, WHITE, RED = "&H746B0F&", "&H37291F&", "&H3BD4FF&", "&HFFFFFF&", "&H3131E0&"
CARD_Y = 900                 # 카드 가운데 높이
CHECK_TOP, CHECK_GAP, MAX_CHECK_ROWS = 600, 25, 4   # 체크 목록 맨 위, 줄 사이, 최대 줄 수
CAPTION_MAX_WIDTH = 980
POP = r"\fscx70\fscy70\t(0,110,\fscx106\fscy106)\t(110,190,\fscx100\fscy100)\fad(0,120)"

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Noto Sans CJK KR,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,9,3,2,50,50,330,1
Style: Card,Noto Sans CJK KR,80,&H0037291F,&H0037291F,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass
class Plan:
    ass: str
    pops: list[float] = field(default_factory=list)     # 카드가 뜨는 시각(효과음 '톡')
    dings: list[float] = field(default_factory=list)    # 체크 항목이 뜨는 시각(효과음 '띵')


def text_width(text: str, size: float) -> float:
    """글자 폭 대략값(한글 0.95em, 숫자·영문 0.58em, 공백 0.3em)."""
    w = 0.0
    for ch in text:
        if ch == " ":
            w += 0.3
        elif ord(ch) < 0x1100:
            w += 0.58
        else:
            w += 0.95
    return w * size


def fit(text: str, max_width: float, max_size: int, min_size: int = 40) -> int:
    width = text_width(text, 1)
    return max(min_size, min(max_size, int(max_width / width))) if width else max_size


def rrect(w: int, h: int, r: int) -> str:
    """둥근 모서리 사각형 그림."""
    return (f"m {r} 0 l {w-r} 0 b {w} 0 {w} 0 {w} {r} l {w} {h-r} b {w} {h} {w} {h} {w-r} {h} "
            f"l {r} {h} b 0 {h} 0 {h} 0 {h-r} l 0 {r} b 0 0 0 0 {r} 0")


def circle(d: int) -> str:
    r = d / 2
    k = r * 0.5523
    return (f"m {r} 0 b {r+k} 0 {d} {r-k} {d} {r} b {d} {r+k} {r+k} {d} {r} {d} "
            f"b {r-k} {d} 0 {r+k} 0 {r} b 0 {r-k} {r-k} 0 {r} 0")


def _ev(layer: int, start: float, end: float, text: str, style: str = "Card") -> str:
    return f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}\n"


def _get(card: Any, key: str) -> str:
    value = card.get(key, "") if isinstance(card, dict) else getattr(card, key, "")
    return str(value or "").strip()


def card_events(start: float, end: float, kind: str, small: str, big: str) -> list[str]:
    color = {"warn": RED, "info": TEAL}.get(kind, TEAL)
    big_size = fit(big, 860, 110 if kind == "info" else 146)
    small_size = fit(small, 860, 74) if small else 0
    height = 110 + big_size + (small_size + 20 if small else 0)
    lines = ([rf"{{\fs{small_size}\1c{DARK}}}{_ass_escape(small)}"] if small else []) + \
            [rf"{{\fs{big_size}\1c{color}}}{_ass_escape(big)}"]
    return [_ev(5, start, end, rf"{{\an5\pos(540,{CARD_Y})\bord0\shad0\1c{WHITE}\p1{POP}}}{rrect(940, height, 36)}"),
            _ev(6, start, end, rf"{{\an5\pos(540,{CARD_Y})\bord0\shad0{POP}}}" + r"\N".join(lines))]


def row_height(small: str) -> int:
    return 200 if small else 150


def check_events(start: float, end: float, y: int, number: int, small: str, big: str) -> list[str]:
    height = row_height(small)
    big_size = fit(big, 700, 76)
    lines = [rf"{{\fs{big_size}\1c{DARK}}}{_ass_escape(big)}"]
    if small:
        lines.append(rf"{{\fs{fit(small, 700, 56)}\1c{TEAL}}}{_ass_escape(small)}")
    return [_ev(5, start, end, rf"{{\an5\pos(540,{y})\bord0\shad0\1c{WHITE}\p1{POP}}}{rrect(940, height, 30)}"),
            _ev(6, start, end, rf"{{\an5\pos(170,{y})\bord0\shad0\1c{TEAL}\p1{POP}}}{circle(96)}"),
            _ev(7, start, end, rf"{{\an5\pos(170,{y})\bord0\shad0\fs60\1c{WHITE}{POP}}}{number}"),
            _ev(7, start, end, rf"{{\an4\pos(250,{y})\bord0\shad0{POP}}}" + r"\N".join(lines))]


_NUM = re.compile(r"(\d[\d,.]*\s?(?:만\s?)?(?:[\d,]+\s?)?(?:원|%|세|명|개|가지|일|월|년)?)")


def caption_markup(text: str) -> str:
    """숫자(단위 포함)는 노란색. 너무 길면 가로로 줄인다."""
    width = text_width(text, 96)
    squeeze = rf"\fscx{int(CAPTION_MAX_WIDTH / width * 100)}" if width > CAPTION_MAX_WIDTH else ""
    body = _NUM.sub(lambda m: rf"{{\1c{YELLOW}}}{m.group(1)}{{\1c{WHITE}}}", _ass_escape(text))
    return r"{\fscx88\fscy88\t(0,90,\fscx100\fscy100)" + (rf"\t(0,90,{squeeze})" if squeeze else "") + "}" + body


def header_events(total: float, topic: str, hook: str, title: str) -> list[str]:
    """위쪽 질문 상자(영상 내내) + 사이트 이름 + 진행 막대."""
    out = [_ev(9, 0, total, rf"{{\an7\pos(0,0)\bord0\shad0\1c{YELLOW}\p1"
                            rf"\clip(0,0,1,16)\t(0,{int(total * 1000)},\clip(0,0,1080,16))}}m 0 0 l 1080 0 l 1080 16 l 0 16")]
    if topic and topic.replace(" ", "") in hook.replace(" ", ""):
        topic = ""                          # 질문에 주제 이름이 이미 있으면 위 줄을 빼서 반복하지 않음
    if hook:
        lines = ([rf"{{\fs{fit(topic, 880, 70)}\1c{YELLOW}}}{_ass_escape(topic)}"] if topic else []) + \
                [rf"{{\fs{fit(hook, 880, 86)}\1c{WHITE}}}{_ass_escape(hook)}"]
    else:                                   # 질문이 없으면 제목을 두 줄로
        from src.tts_engine import wrap_title
        lines = [rf"{{\fs66\1c{WHITE}}}{wrap_title(title)}"]
    out.append(_ev(3, 0, total, rf"{{\an5\pos(540,290)\bord0\shad0\1c{TEAL}\p1}}{rrect(960, 250, 40)}"))
    out.append(_ev(4, 0, total, r"{\an5\pos(540,290)\bord0\shad0}" + r"\N".join(lines)))
    out.append(_ev(4, 0, total, r"{\an5\pos(540,458)\fs40\bord2\shad0\1c&HFFFFFF&\1a&H50&"
                                rf"\3c&H000000&\3a&H90&}}{WATERMARK}"))
    return out


def build_plan(
    scenes: Sequence[tuple[float, float]],
    captions: Sequence[Caption],
    cards: Sequence[Any],
    total: float,
    topic: str = "",
    hook: str = "",
    title: str = "",
) -> Plan:
    """scenes: 장면별 (시작, 끝). cards: 장면별 카드(dict 또는 Card, 없으면 None)."""
    events = header_events(total, topic.strip(), hook.strip(), title.strip())
    pops: list[float] = []
    dings: list[float] = []
    starts = [s for s, _ in scenes]
    nexts = starts[1:] + [total]
    cards = list(cards) + [None] * (len(scenes) - len(cards))
    i = 0
    while i < len(scenes):
        card = cards[i]
        kind = _get(card, "kind") if card else ""
        if not card or not _get(card, "big"):
            i += 1
            continue
        if kind == "check":                 # 연속된 check 장면은 체크 목록으로 쌓는다(최대 4줄씩)
            run = [i]
            while (len(run) < MAX_CHECK_ROWS and run[-1] + 1 < len(scenes)
                   and cards[run[-1] + 1] and _get(cards[run[-1] + 1], "kind") == "check"
                   and _get(cards[run[-1] + 1], "big")):
                run.append(run[-1] + 1)
            end = nexts[run[-1]]
            top = CHECK_TOP
            for n, k in enumerate(run, start=1):
                at = starts[k] + 0.05
                small = _get(cards[k], "small")
                h = row_height(small)
                events += check_events(at, end, top + h // 2, n, small, _get(cards[k], "big"))
                top += h + CHECK_GAP
                dings.append(at)
            i = run[-1] + 1
            continue
        at = starts[i] + 0.05
        events += card_events(at, nexts[i], kind, _get(card, "small"), _get(card, "big"))
        pops.append(at)
        i += 1
    for c in captions:
        events.append(_ev(8, c.start, c.end, caption_markup(c.text), style="Caption"))
    return Plan(HEADER + "".join(events), pops, dings)
