"""deploy/wp/jc-site.css + jc-article.css를 합쳐 워드프레스 '추가 CSS'(사이트 편집 → 스타일)에 넣는다.

- 우리 구간(/* jalcheck:start */ ~ /* jalcheck:end */)만 넣거나 바꾸고, 다른 CSS·스타일은 그대로 둔다.
- 바꾸기 전 현재 설정을 ~/backups/<날짜>-site-css/에 저장한다.
사용: .venv/bin/python scripts/apply_site_css.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import ROOT, load_settings  # noqa: E402
from src.wp_client import WordPressClient  # noqa: E402

START, END = "/* jalcheck:start */", "/* jalcheck:end */"
CSS_FILES = [ROOT / "deploy" / "wp" / "jc-site.css", ROOT / "deploy" / "wp" / "jc-article.css"]  # 순서대로 합침


def merge_css(existing: str, ours: str) -> str:
    block = f"{START}\n{ours.strip()}\n{END}"
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if pattern.search(existing or ""):
        return pattern.sub(lambda _m: block, existing)
    return (existing.rstrip() + "\n\n" + block) if existing.strip() else block


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    wp = WordPressClient.from_settings(load_settings())
    theme = wp._request("GET", "/themes", params={"status": "active"})[0]
    href = theme["_links"]["wp:user-global-styles"][0]["href"]
    gid = int(href.rstrip("/").split("/")[-1])
    current = wp._request("GET", f"/global-styles/{gid}", params={"context": "edit"})
    styles = dict(current.get("styles") or {})
    ours = "\n\n".join(f.read_text(encoding="utf-8").strip() for f in CSS_FILES)
    new_css = merge_css(styles.get("css", ""), ours)
    if new_css == styles.get("css", ""):
        print("이미 최신입니다. 바꿀 것이 없습니다.")
        return 0
    backup = Path.home() / "backups" / f"{datetime.now():%Y-%m-%d}-site-css"
    backup.mkdir(parents=True, exist_ok=True)
    path = backup / f"global-styles-{gid}-{datetime.now():%H%M%S}.json"
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    print("백업:", path)
    if args.dry_run:
        print(f"[dry-run] 테마 {theme['stylesheet']} 전역 스타일 {gid}번에 CSS {len(new_css)}자를 넣을 예정")
        return 0
    # 보내기 직전에 그사이 누가 스타일을 바꿨는지 다시 확인한다(바뀌었으면 덮어쓰지 않고 멈춤)
    latest = wp._request("GET", f"/global-styles/{gid}", params={"context": "edit"})
    if (latest.get("styles") or {}) != (current.get("styles") or {}):
        print("그사이 사이트 스타일이 바뀌었습니다. 덮어쓰지 않고 멈춥니다. 다시 실행해 주세요.")
        return 1
    styles["css"] = new_css
    saved = wp._request("POST", f"/global-styles/{gid}", json={"styles": styles})   # 설정(settings)은 보내지 않음
    ok = START in ((saved.get("styles") or {}).get("css") or "")
    print("적용 완료" if ok else "적용 확인 실패")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
