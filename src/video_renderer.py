"""배경(스톡 영상·사진 또는 움직이는 그라데이션) + 성우 음성 + 자막·제목을 합쳐 1080x1920 쇼츠를 만든다.

- 장면마다 배경을 바꾼다(timeline.json의 장면 시작 시간 기준).
- 서버 CPU가 1개라 렌더링은 한 번에 하나만(파일 잠금), 낮은 우선순위(nice)로 돌린다.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from src.tts_engine import Caption, write_ass

log = logging.getLogger(__name__)

WIDTH, HEIGHT, FPS = 1080, 1920, 30
TAIL_SECONDS = 0.6          # 마지막 대사 뒤 여유
NICE_LEVEL = 15
LOCK_WAIT_SECONDS = 1800
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
# 배경이 없을 때 쓸 그라데이션 색 조합(어두운 톤이라 흰 자막이 잘 보임)
PALETTES = (
    ("0x1d3557", "0x457b9d"), ("0x22223b", "0x4a4e69"), ("0x1b4332", "0x2d6a4f"),
    ("0x3d0e61", "0x6a2c70"), ("0x14213d", "0x274c77"), ("0x2b2d42", "0x3a506b"),
)


class RenderError(RuntimeError):
    """영상 합성 실패."""


@dataclass(frozen=True)
class RenderResult:
    path: Path
    duration: float
    width: int
    height: int
    seconds_taken: float


def _media_kind(path: Path | None) -> str:
    if path is None:
        return "none"
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    raise RenderError(f"배경으로 쓸 수 없는 파일 형식: {path.name}")


def segment_durations(scene_starts: Sequence[float], total: float) -> list[float]:
    """장면 i의 배경은 장면 i 시작부터 다음 장면 시작까지(첫 장면은 0초부터, 마지막은 끝까지)."""
    if not scene_starts:
        raise RenderError("장면이 없습니다")
    bounds = [0.0, *scene_starts[1:], total]
    durations = [round(b - a, 3) for a, b in zip(bounds, bounds[1:])]
    if any(d <= 0 for d in durations):
        raise RenderError(f"장면 시간이 잘못됐습니다: {durations}")
    return durations


def _filter_path(path: Path) -> str:
    """ffmpeg 필터 인자 안에 넣을 경로(특수문자 이스케이프)."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def build_command(
    ffmpeg: Path, media: Sequence[Path | None], durations: Sequence[float], audio: Path,
    ass: Path, fonts_dir: Path, out: Path, palette_seed: str = "",
) -> list[str]:
    if len(media) != len(durations):
        raise RenderError("배경 개수와 장면 개수가 다릅니다")
    total = sum(durations)
    pal_index = int(hashlib.sha1(palette_seed.encode()).hexdigest(), 16) % len(PALETTES)
    cmd = ["nice", "-n", str(NICE_LEVEL), str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
    chains = []
    for i, (path, dur) in enumerate(zip(media, durations)):
        kind = _media_kind(path)
        if kind == "video":
            cmd += ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", str(path)]
            chains.append(
                f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1,trim=duration={dur:.3f},setpts=PTS-STARTPTS,"
                f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.30:t=fill,format=yuv420p[v{i}]")
        elif kind == "image":
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(path)]
            big_w, big_h = int(WIDTH * 1.1), int(HEIGHT * 1.1)
            chains.append(
                f"[{i}:v]scale={big_w}:{big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h},"
                f"crop={WIDTH}:{HEIGHT}:x='(in_w-{WIDTH})*t/{dur:.3f}':y='(in_h-{HEIGHT})/2',"
                f"setsar=1,drawbox=x=0:y=0:w=iw:h=ih:color=black@0.30:t=fill,format=yuv420p[v{i}]")
        else:
            c0, c1 = PALETTES[(pal_index + i) % len(PALETTES)]
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                    f"gradients=s={WIDTH}x{HEIGHT}:r={FPS}:c0={c0}:c1={c1}:speed=0.012:n=2"]
            chains.append(f"[{i}:v]setsar=1,drawbox=x=0:y=0:w=iw:h=ih:color=black@0.20:t=fill,"
                          f"format=yuv420p[v{i}]")
    audio_index = len(media)
    cmd += ["-i", str(audio)]
    joined = "".join(f"[v{i}]" for i in range(len(media)))
    chains.append(f"{joined}concat=n={len(media)}:v=1:a=0[bg]")
    chains.append(f"[bg]ass=filename='{_filter_path(ass)}':fontsdir='{_filter_path(fonts_dir)}'[out]")
    cmd += [
        "-filter_complex", ";".join(chains),
        "-map", "[out]", "-map", f"{audio_index}:a",
        "-af", "apad", "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart", str(out),
    ]
    return cmd


@contextmanager
def render_lock(lock_path: Path, wait: float = LOCK_WAIT_SECONDS) -> Iterator[None]:
    """렌더링은 서버 전체에서 한 번에 하나만."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as f:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise RenderError("다른 렌더링이 너무 오래 걸려 기다리다 멈췄습니다")
                time.sleep(2)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def probe(ffprobe: Path, path: Path) -> dict:
    result = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RenderError(f"결과 영상을 확인할 수 없습니다: {result.stderr.strip()[:200]}")
    return json.loads(result.stdout)


def render_short(
    timeline_path: Path,
    audio_path: Path,
    media: Sequence[Path | None],
    out_path: Path,
    ffmpeg: Path,
    ffprobe: Path,
    fonts_dir: Path,
    title: str | None = None,
    lock_path: Path | None = None,
) -> RenderResult:
    """timeline.json(장면·자막 시간)과 배경 목록으로 쇼츠 mp4를 만든다."""
    timeline = json.loads(Path(timeline_path).read_text(encoding="utf-8"))
    scenes = timeline["scenes"]
    if len(media) != len(scenes):
        raise RenderError(f"배경 {len(media)}개, 장면 {len(scenes)}개로 개수가 다릅니다")
    total = round(float(timeline["duration"]) + TAIL_SECONDS, 3)
    durations = segment_durations([s["start"] for s in scenes], total)
    captions = [Caption(c["text"], c["start"], c["end"]) for c in timeline["captions"]]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path = out_path.with_suffix(".ass")
    write_ass(captions, ass_path, title=title, duration=total)
    tmp_out = out_path.with_name(out_path.stem + ".part.mp4")
    cmd = build_command(ffmpeg, media, durations, audio_path, ass_path, fonts_dir, tmp_out,
                        palette_seed=title or str(out_path))
    lock = lock_path or out_path.parent / ".render.lock"
    started = time.monotonic()
    with render_lock(lock):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=max(300, total * 20))
        except subprocess.TimeoutExpired as exc:
            tmp_out.unlink(missing_ok=True)
            raise RenderError("렌더링 시간이 너무 오래 걸려 멈췄습니다") from exc
    taken = time.monotonic() - started
    if result.returncode != 0:
        tmp_out.unlink(missing_ok=True)
        raise RenderError(f"렌더링 실패: {result.stderr.strip()[-500:]}")

    info = probe(ffprobe, tmp_out)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    duration = float(info.get("format", {}).get("duration", 0))
    if not video or (video.get("width"), video.get("height")) != (WIDTH, HEIGHT):
        raise RenderError(f"결과 영상 크기가 다릅니다: {video}")
    if not has_audio:
        raise RenderError("결과 영상에 소리가 없습니다")
    if abs(duration - total) > 0.6:
        raise RenderError(f"결과 영상 길이({duration:.2f}초)가 예상({total:.2f}초)과 다릅니다")
    tmp_out.replace(out_path)
    log.info("렌더링 완료 %.1f초 영상, %.0f초 걸림", duration, taken)
    return RenderResult(out_path, duration, WIDTH, HEIGHT, taken)
