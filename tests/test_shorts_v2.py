"""쇼츠 개편(2026-11-10): Gemini 음성, 숫자 한글 읽기, 장면 경계, 자막 끊기, 화면 카드, 카드 숫자 검사, 스톡 필터, 음악 출처."""
import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import gemini_tts as G
from src import pipeline
from src.ai_generator import tidy_shorts_cards
from src.config import Settings, load_settings
from src.content_schema import Card, MasterContent
from src.shorts_style import build_plan, caption_markup
from src.stock_media import by_relevance, is_banned
from src.tts_engine import Caption, TTSError
from src.youtube_kit import MUSIC_CREDIT, build_upload_kit
from tests.test_content import good_data

SETTINGS = load_settings()
HAS_FFMPEG = SETTINGS.ffmpeg_bin.exists()


@pytest.mark.parametrize("text, spoken", [
    ("매달 최대 34만 9,700원.", "매달 최대 삼십사만 구천칠백원."),
    ("만 18세 이상", "만 십팔세 이상"),
    ("남은 돈의 30%를", "남은 돈의 삼십 퍼센트를"),
    ("16.5% 공제", "십육 점 오 퍼센트 공제"),
    ("3가지만 보세요", "세가지만 보세요"),
    ("20개월", "이십개월"),
    ("21명", "스물한명"),
    ("6월 1일부터 10월 31일까지", "유월 일일부터 시월 삼십일일까지"),
    ("1만 원, 10,000원, 1억 2,000만 원", "만 원, 만원, 일억 이천만 원"),
    ("2026년", "이천이십육년"),
    ("상담 1588-1234", "상담 일오팔팔 일이삼사"),
    ("12~13세", "십이에서 십삼세"),
    # 2026-11-10 검토 보완: 연도 범위를 전화번호로, 1,2월을 12월로 읽던 문제
    ("2025-2026년", "이천이십오에서 이천이십육년"),
    ("10만~20만 원", "십만에서 이십만 원"),
    ("1,2월", "일,이월"),
    ("02-123-4567", "공이 일이삼 사오육칠"),
    ("2026-11-10", "이천이십육년 십일월 십일"),
    ("2026.12.31.까지", "이천이십육년 십이월 삼십일일까지"),
    ("2026.1.5~2026.2.10", "이천이십육년 일월 오일에서 이천이십육년 이월 십일"),
    ("1일~3일", "일일에서 삼일"),
    # 2026-11-11 Codex 검토 반영: 끝 날짜를 소수로 읽던 문제, 띄어 쓴 %의 단위가 빠지던 문제
    ("2026.1.5~2.10까지 신청", "이천이십육년 일월 오일에서 이월 십일까지 신청"),
    ("2026.1.5.~2.10.까지", "이천이십육년 일월 오일에서 이월 십일까지"),
    ("2026-01-05~02-10", "이천이십육년 일월 오일에서 이월 십일"),
    ("연 3.3 % 적용", "연 삼 점 삼 퍼센트 적용"),
    ("30%~50%", "삼십 퍼센트에서 오십 퍼센트"),
    ("1.5~2.5%", "일 점 오에서 이 점 오 퍼센트"),
])
def test_spoken_text(text, spoken):
    assert G.spoken_text(text) == spoken


def test_find_cuts_picks_pause_near_expected():
    gaps = [(1.0, 1.1), (2.9, 3.4), (3.6, 3.7), (6.0, 6.1), (7.2, 7.6)]
    assert G.find_cuts(gaps, 10.0, ["가" * 3, "나" * 4, "다" * 3]) == [3.15, 7.4]
    with pytest.raises(G.GeminiTTSError):
        G.find_cuts([(0.5, 0.6)], 10.0, ["가" * 5, "나" * 5])


def test_find_cuts_ignores_long_pause_inside_scene():
    """문장 안 쉼표에서 0.9초 쉬고 장면 경계는 0.3초만 쉬어도, 경계를 제자리에서 고른다."""
    spoken = ["가" * 30 + ", " + "나" * 5 + ".", "다" * 35 + "."]
    assert G.find_cuts([(3.0, 3.9), (4.4, 4.7)], 8.2, spoken) == [4.55]


def test_find_cuts_long_pause_without_punctuation_does_not_steal_boundary():
    """문장부호 없는 곳에서 1초 쉬고 실제 경계는 0.2초만 쉬어도 경계를 제자리에서 고른다(Codex 지적)."""
    assert G.find_cuts([(4.5, 5.5), (6.0, 6.2)], 11.2, ["가" * 50, "나" * 50]) == [6.1]


def test_find_cuts_handles_comma_without_pause_and_extra_pause():
    spoken = ["가" * 10 + ", " + "나" * 10 + ".", "다" * 20 + ", " + "라" * 20 + "."]
    # 말한 시간 8초, 글자 60자. 첫 쉼표에서는 안 쉬고, 표시 없는 곳(말한 시간 4초)에서 잠깐 쉼
    gaps = [(2.67, 3.07), (4.4, 4.5), (5.83, 6.03)]
    assert G.find_cuts(gaps, 8.7, spoken) == [2.87]


def test_find_cuts_rejects_wrong_scene_lengths():
    with pytest.raises(G.GeminiTTSError):
        G._check_scene_rates([1.0], 10.0, ["가" * 30, "나" * 30])


def test_scene_captions_break_at_punctuation():
    caps = G.scene_captions("받을 수 있는지, 세 가지만 보세요.", 3.0, 10.0)
    assert [c.text for c in caps] == ["받을 수 있는지", "세 가지만 보세요"]
    assert caps[0].start == pytest.approx(10.0) and caps[0].end == caps[1].start and caps[1].end <= 13.0


def test_save_audio_keeps_wav_file_as_is(tmp_path):
    riff = b"RIFF" + b"\x00" * 40 + b"C2PA-metadata"
    G.save_audio(riff, tmp_path / "a.wav")
    assert (tmp_path / "a.wav").read_bytes() == riff        # 다시 감싸지 않음(감싸면 끝 정보가 '칙' 잡음)
    G.save_audio(b"\x00\x01" * 100, tmp_path / "b.wav")
    with wave.open(str(tmp_path / "b.wav")) as f:
        assert f.getframerate() == G.SAMPLE_RATE and f.getnframes() == 100
    with pytest.raises(G.GeminiTTSError):
        G.save_audio(b"", tmp_path / "c.wav")


def _fake_gemini_wav(ffmpeg: Path, path: Path, pieces: list[float]) -> bytes:
    """말(소리)과 쉼이 번갈아 나오는 WAV + 끝에 출처 정보 덩어리(실제 Gemini 응답처럼)."""
    expr = "+".join(f"between(t,{a},{b})" for a, b in pieces)
    total = pieces[-1][1] + 0.3
    subprocess.run([str(ffmpeg), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='0.3*sin(2*PI*220*t)*({expr})':s=24000:d={total}", "-ac", "1", str(path)], check=True)
    data = path.read_bytes()
    return data + b"C2PA" + bytes(range(256)) * 20


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 없음")
def test_synthesize_gemini_splits_scenes_and_drops_metadata(tmp_path):
    texts = ["첫 장면 대사입니다, 조금 길게.", "둘째 장면 대사예요.", "마지막 장면 대사입니다, 길게 씁니다."]
    pieces = [(0.2, 2.6), (3.3, 5.0), (5.8, 8.6)]               # 장면 사이 0.7~0.8초 쉼
    data = _fake_gemini_wav(SETTINGS.ffmpeg_bin, tmp_path / "src.wav", pieces)
    asked = []
    r = G.synthesize_gemini(texts, tmp_path / "out", "", "m", "Erinome", SETTINGS.ffmpeg_bin,
                            request=lambda t: asked.append(t) or data)
    assert len(asked) == 1 and asked[0].count("\n") == 2       # 한 번에 읽힘
    assert [s.text for s in r.scenes] == texts and len(r.scenes) == 3
    timeline = json.loads(r.timeline_path.read_text(encoding="utf-8"))
    assert timeline["engine"] == "gemini" and timeline["captions"]
    with wave.open(str(r.audio_path)) as f:     # 끝은 부드럽게 줄어들고, 출처 정보가 큰 소리로 붙지 않음
        f.setpos(max(0, f.getnframes() - 240))
        tail = f.readframes(240)
    assert max(abs(int.from_bytes(tail[i:i + 2], "little", signed=True)) for i in range(0, len(tail), 2)) < 4900


def test_synthesize_gemini_rejects_strange_audio(tmp_path):
    with pytest.raises(G.GeminiTTSError):
        G.synthesize_gemini(["대사"], tmp_path, "", "m", "v", Path("/ff"))       # 키 없음


def test_make_tts_falls_back_to_edge(tmp_path):
    calls = []

    def gemini(*a, **k):
        calls.append("gemini")
        raise G.GeminiTTSError("한도")

    def edge(narrations, out, voice, ffmpeg, target_seconds=None):
        calls.append("edge")
        return SimpleNamespace(audio_path=out / "n.wav", timeline_path=out / "t.json", duration=30.0, rate=0)

    content = MasterContent.model_validate(good_data())
    svc = SimpleNamespace(settings=Settings(data_dir=tmp_path, tts_engine="gemini"),
                          synthesize_gemini=gemini, synthesize=edge)
    r = pipeline.make_tts(svc, 1, content, 30)
    assert calls == ["gemini", "edge"] and r["engine"] == "edge"
    svc.settings = Settings(data_dir=tmp_path, tts_engine="edge")
    calls.clear()
    assert pipeline.make_tts(svc, 1, content, 30)["engine"] == "edge" and calls == ["edge"]
    assert issubclass(G.GeminiTTSError, TTSError)


def test_make_tts_falls_back_on_any_gemini_error(tmp_path):
    """시간 초과(TimeoutExpired)·파일 오류처럼 TTSError가 아닌 실패도 기본 음성으로 대체한다."""
    def gemini(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 120)

    def edge(narrations, out, voice, ffmpeg, target_seconds=None):
        return SimpleNamespace(audio_path=out / "n.wav", timeline_path=out / "t.json", duration=30.0, rate=0)

    content = MasterContent.model_validate(good_data())
    svc = SimpleNamespace(settings=Settings(data_dir=tmp_path, tts_engine="gemini"),
                          synthesize_gemini=gemini, synthesize=edge)
    assert pipeline.make_tts(svc, 1, content, 30)["engine"] == "edge"


def test_plan_cards_checks_and_sounds():
    scenes = [(0.0, 3.0), (3.2, 5.0), (5.2, 7.0), (7.2, 9.0), (9.2, 12.0)]
    cards = [Card(kind="number", small="기초급여 월 최대", big="34만 9,700원"), None,
             Card(kind="check", big="만 18세 이상"), {"kind": "check", "big": "중증장애인", "small": "종전 1·2급"},
             Card(kind="warn", big="신청 안 하면 0원")]
    plan = build_plan(scenes, [Caption("매달 34만 원", 0.1, 2.0)], cards, 12.6, topic="장애인연금",
                      hook="나도 받을 수 있을까?", title="제목")
    assert plan.pops == [0.05, 9.25] and plan.dings == [5.25, 7.25]
    assert "34만 9,700원" in plan.ass and "나도 받을 수 있을까?" in plan.ass and "jalcheck.com" in plan.ass
    assert "\\clip(0,0,1080,16)" in plan.ass                       # 진행 막대
    # 체크 목록은 마지막 체크 장면이 끝날 때까지 남는다
    first_check = next(line for line in plan.ass.splitlines() if "만 18세 이상" in line)
    assert ",0:00:05.25,0:00:09.20," in first_check
    # 질문이 없으면 제목을 보여 준다
    assert "제목" in build_plan(scenes, [], [], 12.6, title="제목").ass


def test_caption_numbers_highlighted_and_squeezed():
    assert "{\\1c&H3BD4FF&}34만 9,700원{\\1c&HFFFFFF&}" in caption_markup("매달 최대 34만 9,700원")
    assert "\\fscx" in caption_markup("아주아주아주아주아주 긴 자막입니다요")


def test_tidy_cards_drops_numbers_not_in_narration():
    data = good_data()
    data["shorts"]["hook"] = "나도 받을 수 있을까?"
    data["shorts"]["scenes"][0]["narration"] = "최대 330만 원까지 받을 수 있어요."
    data["shorts"]["scenes"][0]["card"] = {"kind": "number", "big": "330만 원"}
    data["shorts"]["scenes"][1]["card"] = {"kind": "number", "big": "999만 원"}      # 대사에 없는 숫자
    c, warnings = tidy_shorts_cards(MasterContent.model_validate(data))
    assert c.shorts.scenes[0].card.big == "330만 원" and c.shorts.scenes[1].card is None
    assert len(warnings) == 1 and "999만 원" in warnings[0] and c.shorts.hook == "나도 받을 수 있을까?"


def test_banned_stock_tags():
    hits = [{"id": 1, "tags": "hand, cigarette, smoke"}, {"id": 2, "tags": "hand, paper, document"},
            {"id": 3, "tags": "money, dollar, cash"}]
    assert is_banned(hits[0]) and is_banned(hits[2]) and not is_banned(hits[1])
    assert [h["id"] for h in by_relevance(hits, "hands holding papers")] == [2]


def test_stock_filters_ai_look_and_weak_matches():
    assert is_banned({"tags": "computers, office, anime, ai generated"})
    assert is_banned({"tags": "smartphone, green screen, hand"})
    assert is_banned({"tags": "mother and baby, mother tiger, animal care"}, scene=True)
    assert is_banned({"tags": "christmas tree, holiday gifts"}, scene=True)
    # 블로그 사진은 주제에 맞으면 동물·크리스마스도 쓸 수 있다
    assert not is_banned({"tags": "christmas tree, holiday gifts"})
    assert [h["id"] for h in by_relevance([{"id": 7, "tags": "dog, animal, pet"}], "dog")] == [7]
    assert by_relevance([{"id": 7, "tags": "dog, animal, pet"}], "dog", strict=True) == []
    assert not is_banned({"tags": "newborn, baby, mother, family"})
    hits = [{"id": 1, "tags": "sand, hand, beach"}, {"id": 2, "tags": "hands, paperwork, office"}]
    assert [h["id"] for h in by_relevance(hits, "hands reviewing paperwork", strict=True)] == [2]
    assert [h["id"] for h in by_relevance(hits, "hands reviewing paperwork")] == [2, 1]     # 블로그 사진은 기존대로


def test_caption_keeps_amount_together():
    caps = G.scene_captions("산후도우미 비용, 첫째아 기준 최대 152만 5,000원까지 지원돼요.", 5.0, 0.0)
    assert not any(c.text.endswith("152만") for c in caps)
    assert any("152만 5,000원" in c.text for c in caps)


def test_upload_kit_has_music_credit():
    kit = build_upload_kit(MasterContent.model_validate(good_data()), "https://jalcheck.com/a")
    assert MUSIC_CREDIT in kit.description and "Kevin MacLeod" in kit.description
