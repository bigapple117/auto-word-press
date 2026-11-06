"""블로그 주제 후보 찾기.

  .venv/bin/python scripts/find_topics.py --dry-run          # 화면에만 출력(등록·전송·기록 없음)
  .venv/bin/python scripts/find_topics.py --dry-run --no-ai  # Gemini 없이(기본 목록 + 틀 문장)
  .venv/bin/python scripts/find_topics.py --send             # 찾아서 텔레그램으로 보냄(후보 장부에 기록)
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_settings  # noqa: E402
from src.source_finder import NaverSearch  # noqa: E402
from src.topic_finder import (KIND_KO, TopicWriter, find_candidates, load_seeds,  # noqa: E402
                              written_from_jobs)


def read_jobs(path: Path) -> list[dict]:
    """작업 장부를 읽기 전용으로 연다(실행기와 겹쳐도 안전)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT id, topic, status, post_id, created_at FROM jobs")]
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="화면에만 출력(기록·전송 없음)")
    mode.add_argument("--send", action="store_true", help="텔레그램으로 보냄")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    settings = load_settings()
    if args.send:
        from src.ai_generator import ContentGenerator
        from src.telegram_notifier import TelegramNotifier
        from src.topic_service import TopicDeps, run_batch
        deps = TopicDeps(settings, NaverSearch.from_settings(settings),
                         None if args.no_ai else ContentGenerator.from_settings(settings),
                         TelegramNotifier.from_settings(settings))
        print(run_batch(deps, "manual"))
        return 0
    seeds = load_seeds()
    written = written_from_jobs(read_jobs(settings.db_path), seeds)
    writer = None
    if not args.no_ai:
        from src.ai_generator import ContentGenerator
        writer = TopicWriter(ContentGenerator.from_settings(settings))

    result = find_candidates(NaverSearch.from_settings(settings), writer, seeds, written, limit=args.limit)

    print(f"이미 쓴 글로 잡힌 제도: {', '.join(sorted({f'{w.name}({w.year})' for w in written})) or '없음'}\n")
    for i, c in enumerate(result.candidates, 1):
        interest = "-" if c.interest is None else f"{c.interest:,.0f}"
        print(f"{i:>2}. [{KIND_KO[c.kind]}] {c.topic}")
        print(f"    분야 {c.seed.category} · 관심도 {interest} · 점수 {c.score} · 이유: {', '.join(c.reasons) or '-'}")
        if c.official_hosts:
            print(f"    공식 자료: {', '.join(c.official_hosts[:4])}")
        if c.written:
            print(f"    원래 글: 작업 {c.written.job_id}번 · 글 {c.written.post_id} ({c.written.year}년)")
        for t in c.news_titles[:2]:
            print(f"    뉴스: {t[:70]}")
        for w in c.warnings:
            print(f"    ! {w}")
    print()
    for n in result.notes:
        print(f"- {n}")
    print(f"- 네이버 호출 {result.api_calls}번")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
