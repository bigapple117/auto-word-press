"""주제와 참고 주소로 블로그 글 + 쇼츠 대본을 만들어 파일로만 저장한다(발행 안 함).

사용: .venv/bin/python scripts/dry_run_content.py "주제" 주소1 주소2 ... [--seconds 45]
결과: data/dryrun/<시각>/content.json, blog.html, report.txt
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai_generator import ContentGenerator  # noqa: E402
from src.blog_html import render_blog_html  # noqa: E402
from src.config import load_settings  # noqa: E402
from src.source_fetcher import fetch_sources  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--seconds", type=int, default=45)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    settings = load_settings()
    sources, errors = fetch_sources(args.urls)
    for e in errors:
        print("자료 실패:", e)
    if not sources:
        print("읽은 자료가 없어 중단합니다.")
        return 1

    started = time.time()
    result = ContentGenerator.from_settings(settings).generate(args.topic, sources, args.seconds)
    elapsed = time.time() - started

    out = settings.data_dir / "dryrun" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    c = result.content
    (out / "content.json").write_text(c.model_dump_json(indent=2), encoding="utf-8")
    html = f"<h1>{c.title}</h1>\n" + render_blog_html(c, sources)
    (out / "blog.html").write_text(html, encoding="utf-8")
    narration = sum(len(s.narration) for s in c.shorts.scenes)
    report = [
        f"모델: {result.model}, 작성 횟수: {result.attempts}, 걸린 시간: {elapsed:.0f}초",
        f"자료: " + ", ".join(f"{s.id} {s.title}" for s in sources),
        f"제목: {c.title}",
        f"쇼츠 내레이션: {narration}자, 장면 {len(c.shorts.scenes)}개",
        *[f"경고: {w}" for w in result.warnings],
    ]
    (out / "report.txt").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print("저장 위치:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
