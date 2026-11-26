"""읽기 전용 점검: 발행·승인 대기 글의 금액을 최근 기사 '올해 기준' 금액(언론사 2곳 이상 일치)과 견준다.

AI(Gemini)는 쓰지 않는다(네이버 검색 + 직접 읽기·한국 통로만). 글은 고치지 않고 결과만 출력·저장한다.
사용: .venv/bin/python scripts/check_current_values.py [--out data/tmp/current-check.json] [--ids 11,33]
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import date
from html import unescape

sys.path.insert(0, ".")
from src.config import load_settings  # noqa: E402
from src.current_values import amounts, facts, find_current_values  # noqa: E402
from src.source_fetcher import fetch_sources  # noqa: E402
from src.source_finder import NaverSearch  # noqa: E402
from src.wp_client import WordPressClient  # noqa: E402


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def fetch(urls, max_sources=None):
    return fetch_sources(urls, max_sources=max_sources, fallback=None)   # Gemini 대신 읽기 끔


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/tmp/current-check.json")
    ap.add_argument("--ids", default="")
    a = ap.parse_args()
    ids = {int(x) for x in a.ids.split(",") if x.strip()}
    settings = load_settings()
    wp, search = WordPressClient.from_settings(settings), NaverSearch.from_settings(settings)
    rows = sqlite3.connect("data/jobs.sqlite3").execute(
        "select id,status,topic,title,post_id from jobs where post_id is not null "
        "and status in ('published','awaiting_approval') order by id").fetchall()
    report = []
    for jid, st, topic, title, pid in rows:
        if ids and jid not in ids:
            continue
        try:
            body = plain(wp.get_post_content(pid))
        except Exception as exc:  # noqa: BLE001
            print(f"[{jid}] 글 {pid} 읽기 실패: {exc!r}", flush=True)
            continue
        in_post = set(amounts(body))
        cv = find_current_values(search, fetch, topic, 1, today=date.today())
        lines = cv.memo.get("lines") or []
        missing = []
        for ln in lines:
            for label, amt in facts(ln["sentence"]) or []:
                if amt not in in_post:
                    near = []
                    if label:   # 글에서 같은 항목 이름 근처 금액
                        for m in re.finditer(re.escape(label), body):
                            near += amounts(body[m.start(): m.start() + 60])
                    missing.append({"label": label, "amount": amt, "post_near_label": sorted(set(near))})
        item = {"job": jid, "status": st, "post": pid, "topic": topic, "title": title,
                "articles": len(cv.memo.get("articles") or []), "lines": [ln["sentence"] for ln in lines],
                "missing": missing}
        report.append(item)
        mark = "다름" if missing else ("일치" if lines else "기사 없음")
        print(f"[{jid}] {st} 글{pid} {mark} | {title}", flush=True)
        for ln in lines:
            print("    기사:", ln["sentence"][:150], flush=True)
        for m in missing:
            print(f"    ▶ 글에 없음: {m['label'] or '(이름 없음)'} {m['amount']:,}원 / 글 속 같은 항목 금액: "
                  f"{[f'{x:,}' for x in m['post_near_label']]}", flush=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("저장:", a.out)


if __name__ == "__main__":
    main()
