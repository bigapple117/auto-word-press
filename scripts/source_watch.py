"""근거 페이지 변경 감시 장부 보기·지금 확인하기.

  python scripts/source_watch.py show            # 감시 중인 페이지(오래 안 본 순)와 최근 변화
  python scripts/source_watch.py run             # 지금 확인 + 텔레그램 알림(하루 한 번 제한 없이)
  python scripts/source_watch.py check [--limit N]  # 알림 없이 확인만(알릴 변화는 남겨 두었다가 다음 알림 때 보냄)

평소에는 실행기(jalcheck-worker)가 하루 한 번 오후 2시 이후 알아서 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db, source_watch  # noqa: E402
from src.config import load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["show", "run", "check"])
    ap.add_argument("--limit", type=int, default=source_watch.MAX_PER_RUN)
    args = ap.parse_args()
    settings = load_settings()
    if args.action == "show":
        conn = source_watch.connect(source_watch.store_path(settings.data_dir))
        rows = conn.execute("SELECT * FROM pages ORDER BY last_checked IS NOT NULL, last_checked, url").fetchall()
        for r in rows:
            err = f"  실패 {r['fails']}회: {r['last_error'][:60]}" if r["fails"] else ""
            print(f"{(r['last_checked'] or '아직')[:16]:>16}  글 {r['post_id']:>4}  {r['url']}{err}")
        print(f"총 {len(rows)}곳\n\n최근 변화:")
        for c in conn.execute("SELECT * FROM changes ORDER BY id DESC LIMIT 20"):
            mark = "영향" if c["affects"] else "무관"
            print(f"{c['found_at'][:16]}  [{mark}] 글 {c['post_id']}번 {c['summary']}  알림:{c['notified'] or '-'}")
            print(f"    {c['url']}  (없어진 줄 {len(json.loads(c['removed']))}, 새 줄 {len(json.loads(c['added']))})")
        return 0
    from src.pipeline import Services
    services = Services.from_settings(settings)
    deps = source_watch.Deps(services.wp, services.generator, services.telegram, settings.data_dir,
                             lambda: db.connect(settings.db_path))
    print(source_watch.run(deps, send=args.action == "run", limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
