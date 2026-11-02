import json
import subprocess
import threading
import time
import wave
from pathlib import Path

import pytest

from src import video_renderer as vr
from src.config import load_settings
from src.tts_engine import wrap_title, write_ass, Caption

SETTINGS = load_settings()
HAS_FFMPEG = SETTINGS.ffmpeg_bin.exists() and SETTINGS.ffprobe_bin.exists()


def test_segment_durations_cover_whole_video():
    d = vr.segment_durations([0.0, 5.0, 11.2], 20.0)
    assert d == [5.0, 6.2, 8.8] and sum(d) == pytest.approx(20.0)
    # 첫 장면이 0초보다 늦게 시작해도 배경은 0초부터
    assert vr.segment_durations([0.1, 3.0], 6.0) == [3.0, 3.0]


@pytest.mark.parametrize("starts, total", [([], 5.0), ([0.0, 5.0], 4.0), ([0.0, 2.0, 2.0], 5.0)])
def test_bad_timelines_rejected(starts, total):
    with pytest.raises(vr.RenderError):
        vr.segment_durations(starts, total)


def test_media_kind():
    assert vr._media_kind(None) == "none"
    assert vr._media_kind(Path("a.MP4")) == "video"
    assert vr._media_kind(Path("a.jpeg")) == "image"
    with pytest.raises(vr.RenderError):
        vr._media_kind(Path("a.gif"))


def test_build_command_structure(tmp_path):
    cmd = vr.build_command(Path("/ff"), [None, Path("/m/a.mp4"), Path("/m/b.jpg")], [2.0, 3.0, 4.0],
                           Path("/a.wav"), Path("/x:y/c.ass"), Path("/fonts"), tmp_path / "o.mp4", "seed")
    assert cmd[:3] == ["nice", "-n", str(vr.NICE_LEVEL)]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "gradients=" in " ".join(cmd) and "-stream_loop" in cmd and "-loop" in cmd
    assert "concat=n=3:v=1:a=0" in graph
    assert "filename='/x\\:y/c.ass'" in graph                      # 필터 안 경로 이스케이프
    assert cmd[cmd.index("-map") + 3] == "3:a"                       # 음성은 배경 다음 입력
    assert cmd[cmd.index("-t", cmd.index("-af")) + 1] == "9.000"
    with pytest.raises(vr.RenderError):
        vr.build_command(Path("/ff"), [None], [1.0, 2.0], Path("a"), Path("b"), Path("c"), Path("d"))


def test_title_wrap_and_ass(tmp_path):
    assert wrap_title("2026년 근로장려금 신청 자격 핵심 정리 한눈에").count("\\N") == 1
    assert "\\N" not in wrap_title("짧은 제목")
    path = tmp_path / "t.ass"
    write_ass([Caption("자막", 0, 1)], path, title="제목 {위험}", duration=5)
    body = path.read_text(encoding="utf-8")
    assert "Style: Title" in body and ",Title,,0,0,0,,제목 (위험)" in body


def test_render_lock_is_exclusive(tmp_path):
    lock = tmp_path / "r.lock"
    order = []

    def worker(name):
        with vr.render_lock(lock, wait=10):
            order.append(f"{name}-in")
            time.sleep(0.3)
            order.append(f"{name}-out")

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert order[1].endswith("-out") and order[3].endswith("-out")   # 겹치지 않음


def test_render_lock_times_out(tmp_path):
    lock = tmp_path / "r.lock"
    with vr.render_lock(lock):
        with pytest.raises(vr.RenderError, match="기다리다"):
            with vr.render_lock(lock, wait=0):
                pass


def _make_inputs(tmp_path, seconds=(1.0, 1.2)):
    total, starts, gap = 0.0, [], 0.15
    for i, s in enumerate(seconds):
        starts.append(total)
        total += s + (gap if i < len(seconds) - 1 else 0)
    wav = tmp_path / "n.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * round(total * 24000))
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "duration": total,
        "scenes": [{"index": i, "text": "t", "start": st, "end": st + s} for i, (st, s) in enumerate(zip(starts, seconds))],
        "captions": [{"text": "자막 시험", "start": 0.1, "end": 0.9}],
    }), encoding="utf-8")
    return timeline, wav, total


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 없음")
def test_real_render_small(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run([str(SETTINGS.ffmpeg_bin), "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=s=320x240:r=25:d=0.5", str(clip)], check=True)
    timeline, wav, total = _make_inputs(tmp_path)
    out = tmp_path / "out" / "short.mp4"
    r = vr.render_short(timeline, wav, [None, clip], out, SETTINGS.ffmpeg_bin, SETTINGS.ffprobe_bin,
                        SETTINGS.fonts_dir, title="시험 제목")
    assert out.exists() and not out.with_name("short.part.mp4").exists()
    assert (r.width, r.height) == (1080, 1920)
    assert r.duration == pytest.approx(total + vr.TAIL_SECONDS, abs=0.6)


def test_media_count_mismatch(tmp_path):
    timeline, wav, _ = _make_inputs(tmp_path)
    with pytest.raises(vr.RenderError, match="개수"):
        vr.render_short(timeline, wav, [None], tmp_path / "o.mp4", Path("/ff"), Path("/fp"), Path("/f"))


def test_failed_render_leaves_no_output(tmp_path, monkeypatch):
    timeline, wav, _ = _make_inputs(tmp_path)
    monkeypatch.setattr(vr.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "boom"))
    out = tmp_path / "o.mp4"
    with pytest.raises(vr.RenderError, match="렌더링 실패"):
        vr.render_short(timeline, wav, [None, None], out, Path("/ff"), Path("/fp"), Path("/f"))
    assert not out.exists()
