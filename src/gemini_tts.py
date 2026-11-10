"""Gemini 음성으로 쇼츠 대본을 읽히고, 장면별 시간·자막을 만든다(tts_engine.synthesize_scenes와 같은 결과 형식).

- 대본 전체를 한 번에 읽힌다: 장면마다 따로 부르면 톤·빠르기가 매번 달라 목소리가 바뀐 것처럼 들림. 호출도 1번.
- 숫자는 한글로 바꿔 읽힌다(아라비아 숫자는 잘못 읽는 일이 있었음). 자막에는 원래 숫자가 보인다.
- 말투 지시를 붙이지 않는다: 지시문까지 소리 내 읽는 일이 있었음.
- 받은 데이터는 WAV 파일(머리말 + 끝에 C2PA 'AI 생성' 정보)일 수 있다. 소리로 감싸면 그 정보가 '칙' 잡음이 됨.
- 단어별 시간 정보가 없어서, 장면 경계는 쉬는 구간에서 찾고 자막 시간은 글자 수 비율로 정한다.
실패하면 GeminiTTSError → 호출하는 쪽(pipeline)이 edge-tts로 대체한다.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import time
import wave
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Sequence

from src import tts_engine as T

log = logging.getLogger(__name__)

SCENE_GAP = 0.22            # 장면 사이 쉬는 시간(초)
SAMPLE_RATE = 24000
REQUEST_TIMEOUT_MS = 180_000      # 음성 요청 하나의 최대 대기(30초 대본은 보통 10~30초)
MINUTE_LIMIT_WAITS = (30, 45)   # 분당 한도(429)면 쉬었다가 다시
CHARS_PER_SECOND_RANGE = (3.5, 12.0)   # 이 밖이면 이상한 음성(지시문 낭독·잘림 등)으로 본다
EDGE_FADE_TRIM = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.03,areverse,"
             "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.06,areverse,"
             "afade=t=in:d=0.02,areverse,afade=t=in:d=0.05,areverse")


class GeminiTTSError(T.TTSError):
    """Gemini 음성을 쓸 수 없음(한도·오류·이상한 결과). edge-tts로 대체한다."""


# ---------- 숫자를 한글 읽기로 ----------

_DIGITS = "영일이삼사오육칠팔구"
_NATIVE_ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NATIVE_TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
# 이 말 앞의 100 미만 숫자는 고유어로 읽는다(3가지 → 세 가지). 개월은 한자어(3개월 → 삼 개월)
_NATIVE_COUNTERS = ("가지", "개", "명", "곳", "살", "시간", "시", "번째", "군데", "마리", "권", "잔")


def sino(n: int) -> str:
    """한자어 읽기: 349700 → 삼십사만 구천칠백."""
    if n == 0:
        return "영"
    groups, units = [], ["", "만", "억", "조"]
    k = 0
    while n:
        n, g = divmod(n, 10000)
        if g:
            part = ""
            for value, name in ((1000, "천"), (100, "백"), (10, "십"), (1, "")):
                d, g = divmod(g, value)
                if d:
                    part += ("" if d == 1 and name else _DIGITS[d]) + name
            if units[k] == "만" and part == "일":
                part = ""                   # 10000 → '만'
            groups.append(part + units[k])
        k += 1
    return " ".join(reversed(groups))


def native(n: int, before_counter: bool = True) -> str:
    """고유어 읽기(1~99): 3 → 세, 20 → 스무, 21 → 스물한."""
    tens, ones = divmod(n, 10)
    if n == 20 and before_counter:
        return "스무"
    return _NATIVE_TENS[tens] + _NATIVE_ONES[ones]


# 전화번호: 지역·휴대폰(02-123-4567, 010-1234-5678)과 대표번호(1588-1234)만. 2025-2026 같은 연도 범위는 아님
_PHONE = re.compile(r"(?<![\d.])(?:0\d{1,2}-\d{3,4}-\d{4}|1\d{3}-\d{4})(?![\d])")
_DATE = re.compile(r"(?<!\d)(\d{4})[-.](\d{1,2})[-.](\d{1,2})\.?(?![\d])")   # 2026-11-10 → 2026년 11월 10일
# 범위(12~13세, 10만~20만 원, 2025-2026년) → '에서'
_RANGE = re.compile(r"(?<=[\d만천억%])\s*[~～–-]\s*(?=\d)")
# 쉼표는 천 단위 자리(9,700)일 때만 숫자로 묶는다(1,2월은 '일, 이월')
_NUMBER = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")


def spoken_text(text: str) -> str:
    """성우가 읽을 문장: 숫자·기호를 한글 읽기로 바꾼다. 자막은 원래 문장을 쓴다."""
    text = _PHONE.sub(lambda m: " ".join("".join(_DIGITS[int(d)] if d != "0" else "공" for d in part)
                                         for part in m.group().split("-")), text)
    text = _DATE.sub(lambda m: f"{m.group(1)}년 {int(m.group(2))}월 {int(m.group(3))}일", text)
    text = _RANGE.sub("에서 ", text)

    def repl(m: re.Match) -> str:
        whole = m.group(1).replace(",", "")
        frac = m.group(2)
        n = int(whole)
        after = text[m.end():]
        if frac:
            read = sino(n) + " 점 " + "".join(_DIGITS[int(d)] for d in frac[1:])
        elif 0 < n < 100 and after.lstrip().startswith(_NATIVE_COUNTERS) and not after.lstrip().startswith(("개월", "시즌")):
            read = native(n)
        elif after.startswith("월") and n in (6, 10):
            read = "유" if n == 6 else "시"
        elif n == 1 and after[:1] in ("만", "천", "백", "십"):
            read = ""                       # 1만 원 → 만 원
        else:
            read = sino(n)
        if after.startswith("%"):
            read += " 퍼센트"
        return read

    out = _NUMBER.sub(repl, text).replace("%", "")
    return re.sub(r" {2,}", " ", out).strip()


# ---------- 장면 경계·자막 ----------

def find_cuts(gaps: Sequence[tuple[float, float]], duration: float, weights: Sequence[int]) -> list[float]:
    """쉬는 구간(gaps) 중 장면 경계를 고른다. 글자 수로 예상한 위치 근처에서 가장 긴 쉼."""
    cuts, prev = [], 0.0
    total = sum(weights)
    for k in range(1, len(weights)):
        expect = duration * sum(weights[:k]) / total
        near = [g for g in gaps if g[0] > prev + 0.4 and abs((g[0] + g[1]) / 2 - expect) < duration * 0.12]
        if not near:
            raise GeminiTTSError(f"{k}번째 장면 경계를 찾지 못했습니다(예상 {expect:.1f}초)")
        best = max(near, key=lambda g: (g[1] - g[0]) - 0.05 * abs((g[0] + g[1]) / 2 - expect))
        cuts.append(round((best[0] + best[1]) / 2, 3))
        prev = best[1]
    return cuts


_PUNCT = ".,!?…"


def estimated_words(text: str, length: float) -> list[tuple[T.Word, bool]]:
    """단어별 (시간 추정, 뒤에 문장부호 있음). 글자 수 비율로 나누고 문장부호 뒤에는 쉼을 조금 둔다."""
    parts = text.split()
    weights = [len(p) + 0.6 + (1.5 if p[-1] in _PUNCT else 0) for p in parts]
    total, pos, out = sum(weights) or 1, 0.0, []
    for p, w in zip(parts, weights):
        start = length * pos / total
        pos += w
        clean = p.rstrip(_PUNCT)
        speak = len(clean) + 0.6
        out.append((T.Word(clean, start, start + length * speak / total), clean != p))
    return out


def scene_captions(text: str, length: float, offset: float) -> list[T.Caption]:
    """쉼표·마침표에서 먼저 끊고, 긴 구절만 글자 수로 고르게 나눈다(단위 앞에서는 안 끊음)."""
    phrases, current = [], []
    for word, punct in estimated_words(text, length):
        if word.text:
            current.append(word)
        if punct and current:
            phrases.append(current)
            current = []
    if current:
        phrases.append(current)
    caps = [T.Caption(" ".join(w.text for w in chunk), offset + chunk[0].start, offset + chunk[-1].end)
            for phrase in phrases for chunk in T._split_phrase(phrase)]
    for a, b in zip(caps, caps[1:]):
        a.end = b.start                     # 다음 자막까지 이어서(깜빡임 없음)
    return caps


# ---------- 합성 ----------

def _request_audio(api_key: str, model: str, voice: str, text: str, sleep: Callable = time.sleep) -> bytes:
    from google import genai
    from google.genai import types
    # 응답 제한 시간: 기본값은 무제한이라 접속이 멈추면 실행기 전체가 멈춘다(넘으면 edge-tts로 대체)
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))))
    for attempt in range(len(MINUTE_LIMIT_WAITS) + 1):
        try:
            r = client.models.generate_content(model=model, contents=text, config=config)
            return r.candidates[0].content.parts[0].inline_data.data
        except Exception as exc:            # 한도·접속 오류 모두 edge-tts로 대체하면 되므로 넓게 받는다
            msg = str(exc)
            per_day = "PerDay" in msg or "per day" in msg.lower()
            if "429" in msg and not per_day and attempt < len(MINUTE_LIMIT_WAITS):
                log.info("Gemini 음성 분당 한도, %s초 쉬고 다시", MINUTE_LIMIT_WAITS[attempt])
                sleep(MINUTE_LIMIT_WAITS[attempt])
                continue
            raise GeminiTTSError(f"Gemini 음성 실패: {msg[:200]}") from exc
    raise GeminiTTSError("Gemini 음성 실패")


def save_audio(data: bytes, path: Path) -> None:
    """받은 데이터를 WAV로 저장. 이미 WAV 파일이면 그대로(머리말·출처 정보를 소리로 감싸지 않게)."""
    if not data:
        raise GeminiTTSError("Gemini 음성이 비어 있습니다")
    if data[:4] == b"RIFF":
        path.write_bytes(data)
        return
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(data)


def _ffmpeg(ffmpeg: Path, *args: str) -> str:
    r = subprocess.run([str(ffmpeg), "-hide_banner", *args], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise GeminiTTSError(f"음성 처리 실패: {r.stderr.strip()[-200:]}")
    return r.stderr


def silences(ffmpeg: Path, wav: Path) -> list[tuple[float, float]]:
    log_text = _ffmpeg(ffmpeg, "-i", str(wav), "-af", "silencedetect=noise=-42dB:d=0.1", "-f", "null", "-")
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", log_text)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", log_text)]
    return list(zip(starts, ends))


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as f:
        return f.getnframes() / f.getframerate()


def synthesize_gemini(
    narrations: Sequence[str],
    out_dir: Path,
    api_key: str,
    model: str,
    voice: str,
    ffmpeg: Path,
    request: Callable[[str], bytes] | None = None,
) -> T.TTSResult:
    """장면별 대사(자막용 원문)를 Gemini 음성으로. 결과 형식은 tts_engine.synthesize_scenes와 같다."""
    narrations = [n.strip() for n in narrations if n and n.strip()]
    if not narrations:
        raise T.TTSError("읽을 대사가 없습니다")
    if not api_key and request is None:
        raise GeminiTTSError("GEMINI_API_KEY가 없습니다")
    out_dir.mkdir(parents=True, exist_ok=True)
    spoken = [spoken_text(n) for n in narrations]
    fetch = request or (lambda text: _request_audio(api_key, model, voice, text))
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        tmpdir = Path(tmp)
        got, full = tmpdir / "gemini.wav", tmpdir / "full.wav"
        save_audio(fetch("\n".join(spoken)), got)
        _ffmpeg(ffmpeg, "-loglevel", "error", "-y", "-i", str(got), "-af", EDGE_FADE_TRIM,
                "-ar", str(SAMPLE_RATE), "-ac", "1", str(full))
        length = _wav_seconds(full)
        chars = sum(len(s.replace(" ", "")) for s in spoken)
        if not CHARS_PER_SECOND_RANGE[0] <= chars / max(length, 0.1) <= CHARS_PER_SECOND_RANGE[1]:
            raise GeminiTTSError(f"음성 길이({length:.1f}초)가 대본({chars}자)과 맞지 않습니다")
        weights = [len(s.replace(" ", "")) for s in spoken]
        cuts = find_cuts(silences(ffmpeg, full), length, weights)
        bounds = [0.0, *cuts, None]
        parts = []
        for i in range(len(spoken)):
            seg = tmpdir / f"s{i}.wav"
            rng = f"atrim=start={bounds[i]:.3f}" + (f":end={bounds[i + 1]:.3f}" if bounds[i + 1] else "")
            _ffmpeg(ffmpeg, "-loglevel", "error", "-y", "-i", str(full), "-af",
                    f"{rng},asetpts=PTS-STARTPTS,{EDGE_FADE_TRIM}", "-ar", str(SAMPLE_RATE), "-ac", "1", str(seg))
            if _wav_seconds(seg) < 0.3:
                raise GeminiTTSError(f"{i + 1}번째 장면 음성이 너무 짧습니다")
            parts.append(seg)
        audio_path = out_dir / "narration.wav"
        spans = T._join_wavs(parts, audio_path, SCENE_GAP)
    duration = spans[-1][1]
    scenes, captions = [], []
    for i, ((start, end), text) in enumerate(zip(spans, narrations)):
        scenes.append(T.SceneTiming(i, text, start, end))
        captions += scene_captions(text, end - start, start)
    ass_path = out_dir / "captions.ass"
    T.write_ass(captions, ass_path)
    timeline_path = out_dir / "timeline.json"
    timeline_path.write_text(json.dumps({
        "duration": duration, "rate": 0, "voice": f"gemini:{voice}", "engine": "gemini",
        "scenes": [asdict(s) for s in scenes], "captions": [asdict(c) for c in captions],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Gemini 음성 %.1f초(%s), 장면 경계 %s", duration, voice, cuts)
    return T.TTSResult(audio_path, ass_path, timeline_path, duration, 0, scenes, captions)
