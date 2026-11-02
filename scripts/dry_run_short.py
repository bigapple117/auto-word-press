"""dry run 결과 폴더(content.json)로 쇼츠 영상을 만든다(발행 안 함).

사용: .venv/bin/python scripts/dry_run_short.py data/dryrun/<폴더> [--seconds 45] [--no-stock]
결과: <폴더>/short/short.mp4, narration.wav, timeline.json, media.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_settings  # noqa: E402
from src.content_schema import MasterContent  # noqa: E402
from src.stock_media import StockMediaPicker  # noqa: E402
from src.tts_engine import synthesize_scenes  # noqa: E402
from src.video_renderer import render_short  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--seconds", type=int, default=45)
    ap.add_argument("--no-stock", action="store_true", help="배경 영상 없이 그라데이션만")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    settings = load_settings()
    content = MasterContent.model_validate_json((args.folder / "content.json").read_text(encoding="utf-8"))
    out = args.folder / "short"
    scenes = content.shorts.scenes

    t0 = time.time()
    tts = synthesize_scenes([s.narration for s in scenes], out, settings.tts_voice,
                            settings.ffmpeg_bin, target_seconds=args.seconds)
    t1 = time.time()
    if args.no_stock:
        picks = [None] * len(scenes)
    else:
        picks = StockMediaPicker.from_settings(settings).pick_scene_backgrounds([s.video_query for s in scenes])
    t2 = time.time()
    (out / "media.json").write_text(json.dumps([
        None if p is None else {"kind": p.kind, "id": p.media_id, "query": p.query, "page": p.page_url,
                                "author": p.author, "file": str(p.path)} for p in picks
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    result = render_short(tts.timeline_path, tts.audio_path, [p.path if p else None for p in picks],
                          out / "short.mp4", settings.ffmpeg_bin, settings.ffprobe_bin, settings.fonts_dir,
                          title=content.shorts.title)
    print(f"성우·자막 {t1 - t0:.0f}초, 배경 찾기 {t2 - t1:.0f}초, 합성 {result.seconds_taken:.0f}초")
    print(f"영상 길이 {result.duration:.1f}초, 말 속도 {tts.rate:+d}%")
    for scene, pick in zip(scenes, picks):
        print(f"- {scene.video_query!r} → {pick.kind + ' ' + str(pick.media_id) + ' (' + pick.author + ')' if pick else '그라데이션'}")
    print("저장 위치:", result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
