"""쇼츠 대본을 AI 성우(edge-tts)로 읽히고, 말하는 타이밍에 맞춘 .ass 자막을 만든다.

- 장면마다 따로 합성해 장면별 시작·끝 시간을 정확히 안다(배경 영상 전환에 씀).
- 단어별 시간 정보로 자막을 짧게 끊는다(문장부호, 쉬는 구간, 글자 수 기준).
- 전체 길이가 목표보다 많이 길면 말 속도를 올려 다시 합성한다(최대 +25%).
결과: narration.wav, captions.ass, timeline.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import subprocess
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

import edge_tts

log = logging.getLogger(__name__)

SCENE_GAP = 0.15            # 장면 사이 쉬는 시간(초)
MAX_CAPTION_CHARS = 13      # 자막 한 덩어리 최대 글자 수(공백 제외)
PAUSE_BREAK = 0.35          # 단어 사이가 이만큼 비면 자막을 끊음
MAX_RATE = 25               # 말 속도 최대 +25%
RETRY_WAITS = (2, 5, 10)
_TICKS = 10_000_000         # edge-tts 시간 단위(100ns)


class TTSError(RuntimeError):
    """음성 합성 실패."""


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Caption:
    text: str
    start: float
    end: float


@dataclass
class SceneTiming:
    index: int
    text: str
    start: float
    end: float


@dataclass
class TTSResult:
    audio_path: Path
    ass_path: Path
    timeline_path: Path
    duration: float
    rate: int
    scenes: list[SceneTiming] = field(default_factory=list)
    captions: list[Caption] = field(default_factory=list)


# ---------- 자막 덩어리 만들기 ----------

def _attach_punctuation(text: str, words: Sequence[Word]) -> list[tuple[Word, bool]]:
    """원문에서 단어 위치를 차례로 찾아, 단어 뒤에 문장부호가 있는지(끊을 자리인지) 표시한다."""
    out = []
    cursor = 0
    for w in words:
        pos = text.find(w.text, cursor)
        if pos < 0:
            out.append((w, False))
            continue
        end = pos + len(w.text)
        tail = text[end:end + 2]
        out.append((w, bool(re.match(r"[.,!?…·:;]", tail))))
        cursor = end
    return out


# 이 말로 시작하는 짧은 단어 앞에서는 끊지 않는다(숫자와 단위가 갈라지지 않게)
_UNIT_PREFIXES = ("원", "일", "월", "년", "개월", "세", "명", "퍼센트", "만", "억", "천", "시", "분", "초", "회", "배")


_NUMBER_WORD = re.compile(r"[일이삼사오육칠팔구십백천만억조영공\d,.]+")


def _no_break_before(word: str) -> bool:
    return len(word) <= 4 and word.startswith(_UNIT_PREFIXES)


def _is_number(word: str) -> bool:
    """'이억', '사천만'처럼 숫자만으로 된 단어."""
    return bool(_NUMBER_WORD.fullmatch(word))


def _split_phrase(words: list[Word]) -> list[list[Word]]:
    """긴 구절을 글자 수가 고르게 나눈다. 단위 앞에서는 끊지 않는다."""
    total = sum(len(w.text) for w in words)
    if total <= MAX_CAPTION_CHARS or len(words) == 1:
        return [words]
    parts = math.ceil(total / MAX_CAPTION_CHARS)
    target = total / parts
    out, current, size = [], [], 0
    for i, w in enumerate(words):
        current.append(w)
        size += len(w.text)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None:
            break
        remaining_parts = parts - len(out) - 1
        if remaining_parts <= 0 or _no_break_before(nxt.text):
            continue
        if _is_number(w.text) and _is_number(nxt.text):   # '이억 사천만'은 한 덩어리
            continue
        # 목표 길이에 가까워졌고, 다음 단어를 더하면 목표에서 더 멀어지면 끊는다
        if abs(size - target) <= abs(size + len(nxt.text) - target):
            out.append(current)
            current, size = [], 0
    out.append(current)
    return out


def build_captions(text: str, words: Sequence[Word], offset: float = 0.0) -> list[Caption]:
    """단어 시간 정보로 짧은 자막 덩어리를 만든다.

    1) 문장부호·쉬는 구간에서 구절로 나누고 2) 긴 구절은 고르게 나눈다.
    """
    phrases: list[list[Word]] = []
    current: list[Word] = []
    for w, punct_after in _attach_punctuation(text, words):
        if current and w.start - current[-1].end >= PAUSE_BREAK and not _no_break_before(w.text):
            phrases.append(current)
            current = []
        current.append(w)
        if punct_after:
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)

    captions = [
        Caption(" ".join(w.text for w in chunk), offset + chunk[0].start, offset + chunk[-1].end)
        for phrase in phrases for chunk in _split_phrase(phrase)
    ]
    # 다음 자막이 나오기 전까지 이어서 보여 줘 깜빡임을 줄인다(최대 0.3초)
    for a, b in zip(captions, captions[1:]):
        a.end = min(b.start, a.end + 0.3)
    return captions


# ---------- ASS 자막 파일 ----------

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Noto Sans CJK KR,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(captions: Sequence[Caption], path: Path) -> None:
    lines = [ASS_HEADER]
    for c in captions:
        lines.append(f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Caption,,0,0,0,,"
                     f"{_ass_escape(c.text)}\n")
    path.write_text("".join(lines), encoding="utf-8")


# ---------- 합성 ----------

async def _synthesize_once(text: str, voice: str, rate: int, mp3_path: Path) -> list[Word]:
    comm = edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", boundary="WordBoundary",
                                connect_timeout=15, receive_timeout=60)
    words: list[Word] = []
    with mp3_path.open("wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / _TICKS
                words.append(Word(chunk["text"], start, start + chunk["duration"] / _TICKS))
    if mp3_path.stat().st_size == 0:
        raise TTSError("음성 데이터가 비어 있습니다")
    return words


def _synthesize(text: str, voice: str, rate: int, mp3_path: Path, sleep=time.sleep) -> list[Word]:
    last: Exception | None = None
    for attempt in range(len(RETRY_WAITS) + 1):
        try:
            return asyncio.run(_synthesize_once(text, voice, rate, mp3_path))
        except Exception as exc:  # noqa: BLE001 - 네트워크·서비스 오류는 모두 재시도
            last = exc
            if attempt < len(RETRY_WAITS):
                log.warning("성우 합성 실패(%s), %s초 후 재시도", exc.__class__.__name__, RETRY_WAITS[attempt])
                sleep(RETRY_WAITS[attempt])
    raise TTSError(f"성우 합성 실패: {last}") from last


def _to_wav(ffmpeg: Path, mp3: Path, wav: Path) -> None:
    cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp3),
           "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(wav)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise TTSError(f"오디오 변환 실패: {result.stderr.strip()[:300]}")


def _join_wavs(parts: Sequence[Path], out: Path, gap: float) -> list[tuple[float, float]]:
    """wav들을 쉬는 구간을 넣어 이어 붙이고, 각 조각의 (시작, 끝) 시간을 돌려준다."""
    spans = []
    with wave.open(str(parts[0])) as first:
        params = first.getparams()
    rate = params.framerate
    silence = b"\x00" * (round(gap * rate) * params.sampwidth * params.nchannels)
    position = 0
    with wave.open(str(out), "wb") as dst:
        dst.setparams(params)
        for i, part in enumerate(parts):
            with wave.open(str(part)) as src:
                if src.getframerate() != rate or src.getnchannels() != params.nchannels:
                    raise TTSError("장면 음성 형식이 서로 다릅니다")
                frames = src.readframes(src.getnframes())
                count = src.getnframes()
            start = position / rate
            dst.writeframes(frames)
            position += count
            spans.append((start, position / rate))
            if i < len(parts) - 1:
                dst.writeframes(silence)
                position += len(silence) // (params.sampwidth * params.nchannels)
    return spans


def synthesize_scenes(
    narrations: Sequence[str],
    out_dir: Path,
    voice: str,
    ffmpeg: Path,
    target_seconds: float | None = None,
    sleep=time.sleep,
) -> TTSResult:
    """장면별 대사를 합성해 하나의 음성 파일, 자막, 타임라인을 만든다."""
    narrations = [n.strip() for n in narrations if n and n.strip()]
    if not narrations:
        raise TTSError("읽을 대사가 없습니다")
    out_dir.mkdir(parents=True, exist_ok=True)
    rate = 0
    for _pass in range(2):
        with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
            tmpdir = Path(tmp)
            wavs, word_lists = [], []
            for i, text in enumerate(narrations):
                mp3, wav = tmpdir / f"s{i}.mp3", tmpdir / f"s{i}.wav"
                word_lists.append(_synthesize(text, voice, rate, mp3, sleep=sleep))
                _to_wav(ffmpeg, mp3, wav)
                wavs.append(wav)
            audio_path = out_dir / "narration.wav"
            spans = _join_wavs(wavs, audio_path, SCENE_GAP)
        duration = spans[-1][1]
        too_long = target_seconds and duration > target_seconds * 1.12
        if not too_long or rate >= MAX_RATE:
            break
        rate = min(MAX_RATE, rate + math.ceil((duration / target_seconds - 1) * 100) + 2)
        log.info("음성이 %.1f초로 목표보다 길어 말 속도 %+d%%로 다시 합성", duration, rate)

    scenes, captions = [], []
    for i, ((start, end), text, words) in enumerate(zip(spans, narrations, word_lists)):
        scenes.append(SceneTiming(i, text, start, end))
        captions += build_captions(text, words, offset=start)
    ass_path = out_dir / "captions.ass"
    write_ass(captions, ass_path)
    timeline_path = out_dir / "timeline.json"
    timeline_path.write_text(json.dumps({
        "duration": duration, "rate": rate, "voice": voice,
        "scenes": [asdict(s) for s in scenes], "captions": [asdict(c) for c in captions],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return TTSResult(audio_path, ass_path, timeline_path, duration, rate, scenes, captions)
