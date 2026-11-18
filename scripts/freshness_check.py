"""글 '다시 볼 날짜' 장부 보기·지금 점검하기.

  python scripts/freshness_check.py show          # 장부(다시 볼 날짜순)
  python scripts/freshness_check.py run           # 날짜 정하기 + 지난 글 텔레그램 알림(하루 한 번 제한 없이 지금)
  python scripts/freshness_check.py plan          # 날짜만 정하고 알림은 보내지 않음

평소에는 실행기(jalcheck-worker)가 하루 한 번 오전 10시 이후 알아서 한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import freshness  # noqa: E402
from src.config import load_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["show", "run", "plan"])
    args = ap.parse_args()
    settings = load_settings()
    if args.action == "show":
        rows = freshness.schedule_rows(settings.data_dir)
        for r in rows:
            print(f"{r['recheck_on']}  글 {r['post_id']:>4}  {r['title']}")
            for i in json.loads(r["items"]):
                print(f"            - {i['recheck_on']} {i['why']}  ({i['what']})")
        print(f"총 {len(rows)}편")
        return 0
    from src.pipeline import Services
    services = Services.from_settings(settings)
    deps = freshness.Deps(services.wp, services.generator, services.telegram, settings.data_dir)
    print(freshness.run(deps, notify=args.action == "run"))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
