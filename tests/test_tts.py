import json
import wave

import pytest

from src import tts_engine
from src.tts_engine import (Caption, TTSError, Word, _ass_time, build_captions,
                            synthesize_scenes, write_ass)


def words_of(text, step=0.35, gaps=None):
    """공백 기준 단어를 일정 간격으로 배치한다. gaps={단어 위치: 추가 쉼}."""
    t, out = 0.0, []
    for i, w in enumerate(text.replace(",", "").replace(".", "").replace("?", "").split()):
        t += (gaps or {}).get(i, 0)
        out.append(Word(w, t, t + 0.3))
        t += step
    return out


def texts(caps):
    return [c.text for c in caps]


def test_breaks_after_punctuation():
    text = "근로장려금, 최대 금액은? 홈택스에서 신청합니다."
    assert texts(build_captions(text, words_of(text))) == ["근로장려금", "최대 금액은", "홈택스에서 신청합니다"]


def test_long_phrase_split_evenly_and_units_stay_with_numbers():
    text = "재산은 가구원 합산 이억 사천만 원 미만이어야 하고 빚은 빼주지 않습니다."
    caps = texts(build_captions(text, words_of(text)))
    assert "이억 사천만 원" in " / ".join(caps)
    assert all(not c.startswith("원") for c in caps)
    assert all(len(c.replace(" ", "")) <= tts_engine.MAX_CAPTION_CHARS + 6 for c in caps)


def test_pause_starts_new_caption():
    text = "신청은 홈택스에서 합니다"
    caps = build_captions(text, words_of(text, gaps={2: 0.6}))
    assert texts(caps) == ["신청은 홈택스에서", "합니다"]


def test_offset_and_hold_until_next():
    text = "첫 문장. 둘째 문장."
    caps = build_captions(text, words_of(text), offset=10)
    assert caps[0].start == pytest.approx(10.0)
    assert caps[0].end <= caps[1].start
    assert caps[0].end == pytest.approx(min(caps[1].start, 10 + 0.65 + 0.3))


def test_words_missing_from_text_do_not_crash():
    caps = build_captions("전혀 다른 글", [Word("없는단어", 0, 0.3), Word("또", 0.4, 0.6)])
    assert texts(caps) == ["없는단어 또"]


def test_ass_file(tmp_path):
    path = tmp_path / "c.ass"
    write_ass([Caption("{\\b1}괄호\\와 줄\n바꿈", 3725.456, 3726.0)], path)
    body = path.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in body and "Noto Sans CJK KR" in body
    line = body.strip().splitlines()[-1]
    assert line.startswith("Dialogue: 0,1:02:05.46,1:02:06.00,Caption")
    assert "{" not in line.split("Caption,,0,0,0,,")[1]  # 자막 명령으로 해석되지 않게
    assert _ass_time(0) == "0:00:00.00"


# ---------- 합성 흐름 (가짜 성우) ----------

def fake_tts(monkeypatch, seconds_per_char=0.16, fail_times=0):
    calls = []

    def fake_synthesize(text, voice, rate, mp3_path, sleep=None):
        calls.append(rate)
        mp3_path.write_bytes(text.encode())   # _to_wav 가짜가 길이를 계산할 수 있게 글자를 저장
        return words_of(text)

    def fake_to_wav(ffmpeg, mp3, wav):
        text = mp3.read_bytes().decode()
        rate = calls[-1]
        seconds = len(text) * seconds_per_char / (1 + rate / 100)
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00" * round(seconds * 24000))

    monkeypatch.setattr(tts_engine, "_synthesize", fake_synthesize)
    monkeypatch.setattr(tts_engine, "_to_wav", fake_to_wav)
    return calls


def test_synthesize_scenes_builds_timeline(tmp_path, monkeypatch):
    fake_tts(monkeypatch)
    r = synthesize_scenes(["첫 장면 대사입니다.", "", "둘째 장면 대사입니다."], tmp_path, "v", tmp_path / "ffmpeg")
    assert [s.index for s in r.scenes] == [0, 1]                     # 빈 대사는 뺌
    first, second = r.scenes
    assert second.start == pytest.approx(first.end + tts_engine.SCENE_GAP, abs=1e-3)
    assert r.duration == pytest.approx(second.end)
    with wave.open(str(r.audio_path)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(r.duration, abs=1e-3)
    assert r.captions[0].start >= 0 and r.captions[-1].start >= second.start
    tl = json.loads(r.timeline_path.read_text(encoding="utf-8"))
    assert tl["duration"] == pytest.approx(r.duration) and len(tl["scenes"]) == 2
    assert r.ass_path.exists()


def test_too_long_audio_is_resynthesized_faster(tmp_path, monkeypatch):
    calls = fake_tts(monkeypatch, seconds_per_char=0.5)
    r = synthesize_scenes(["가" * 100], tmp_path, "v", tmp_path / "ffmpeg", target_seconds=40)
    assert calls[0] == 0 and calls[-1] > 0 and r.rate == calls[-1] <= tts_engine.MAX_RATE


def test_no_text_is_error(tmp_path):
    with pytest.raises(TTSError):
        synthesize_scenes(["", "  "], tmp_path, "v", tmp_path / "ffmpeg")


def test_retry_then_fail(monkeypatch, tmp_path):
    attempts = []

    async def broken(*a, **k):
        attempts.append(1)
        raise ConnectionError("down")

    monkeypatch.setattr(tts_engine, "_synthesize_once", broken)
    with pytest.raises(TTSError, match="성우 합성 실패"):
        tts_engine._synthesize("안녕", "v", 0, tmp_path / "a.mp3", sleep=lambda s: None)
    assert len(attempts) == len(tts_engine.RETRY_WAITS) + 1
