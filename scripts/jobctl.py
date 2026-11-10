"""작업 관리 도구.

  .venv/bin/python scripts/jobctl.py add "주제" [--mode blog_shorts|blog] [--seconds 30]
  .venv/bin/python scripts/jobctl.py list [--status awaiting_approval]
  .venv/bin/python scripts/jobctl.py show 번호
  .venv/bin/python scripts/jobctl.py retry 번호      # 실패·취소된 작업을 다시 대기열로(끝난 단계는 건너뜀)
  .venv/bin/python scripts/jobctl.py cancel 번호
  .venv/bin/python scripts/jobctl.py once            # 대기 작업 하나만 처리(버튼 수신 없음)
  .venv/bin/python scripts/jobctl.py run             # 상시 실행(작업 처리 + 텔레그램 버튼 수신)
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db  # noqa: E402
from src.config import load_settings  # noqa: E402

STATUS_KO = {"queued": "대기", "generating": "1단계:자료·글", "tts": "2단계:성우", "media": "3단계:사진·영상",
             "rendering": "4단계:합성·임시글", "awaiting_approval": "승인대기", "publishing": "발행 중",
             "published": "발행완료", "failed": "실패", "canceled": "취소"}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("topic")
    a.add_argument("--mode", default="blog_shorts", choices=["blog_shorts", "blog"])
    a.add_argument("--seconds", type=int, default=30)
    a.add_argument("--ref", default=None, help="중복 등록 방지용 출처 번호(예: 시트 행)")
    lst = sub.add_parser("list")
    lst.add_argument("--status", default=None)
    for name in ("show", "retry", "cancel", "redo", "redraft"):
        sub.add_parser(name).add_argument("job_id", type=int)
    sub.add_parser("once")
    sub.add_parser("run")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    settings = load_settings()
    conn = db.connect(settings.db_path)

    if args.cmd == "add":
        job_id = db.create_job(conn, args.topic, args.mode, args.seconds, source="cli", source_ref=args.ref)
        print(f"작업 {job_id}번 등록: {args.topic} ({args.mode}, {args.seconds}초)")
    elif args.cmd == "list":
        for j in db.list_jobs(conn, status=args.status, limit=50):
            print(f"{j['id']:>4}  {STATUS_KO.get(j['status'], j['status']):<12} {j['topic'][:30]:<30} "
                  f"글 {j['post_id'] or '-'}  {j['updated_at']}" + (f"\n      오류: {j['error']}" if j["error"] else ""))
    elif args.cmd == "show":
        j = db.get_job(conn, args.job_id)
        if not j:
            print("없는 작업입니다")
            return 1
        cp = j.pop("checkpoint")
        if "content" in cp:
            cp["content"] = {k: v for k, v in cp["content"].items() if k in ("warnings", "source_errors", "model")}
            cp["content"]["title"] = j.get("title")
        print(json.dumps({**j, "checkpoint": cp}, ensure_ascii=False, indent=2))
    elif args.cmd == "retry":
        db.set_status(conn, args.job_id, "queued")
        print(f"작업 {args.job_id}번을 다시 대기열에 넣었습니다")
    elif args.cmd == "redo":
        # 글을 처음부터 다시 쓴다: 같은 워드프레스 임시 글을 고쳐 쓰고, 예전 승인 요청 버튼은 무효가 된다
        from src.wp_client import WordPressClient
        j = db.get_job(conn, args.job_id)
        if not j or j["status"] in ("published", "publishing"):
            print("다시 쓸 수 없는 작업입니다(없거나 이미 발행)")
            return 1
        if j["status"] not in ("failed", "canceled"):
            db.set_status(conn, args.job_id, "canceled")
        removed = WordPressClient.from_settings(settings).delete_job_media(args.job_id)
        db.clear_checkpoint(conn, args.job_id, ("content", "tts", "media", "video", "draft", "approval"))
        db.set_status(conn, args.job_id, "queued")
        print(f"작업 {args.job_id}번을 다시 쓰기 대기열에 넣었습니다(예전 사진 {len(removed)}장 삭제, 임시 글 번호 유지)")
    elif args.cmd == "redraft":
        # 글 내용·쇼츠는 그대로 두고 HTML(디자인)만 다시 만들어 같은 임시 글에 반영, 승인 요청은 새로
        from src.ai_generator import check_content
        from src.content_schema import MasterContent
        from src.pipeline import sources_from
        j = db.get_job(conn, args.job_id)
        if not j or j["status"] in ("published", "publishing") or "content" not in j["checkpoint"]:
            print("다시 만들 수 없는 작업입니다(없거나 이미 발행, 또는 글이 아직 없음)")
            return 1
        cp = j["checkpoint"]["content"]
        content = MasterContent.model_validate(cp["content"])
        _problems, warnings = check_content(content, sources_from(cp["sources"]), j["target_seconds"])
        if cp.get("fallback_model"):
            warnings.insert(0, next(w for w in cp["warnings"] if w.startswith("예비 모델")))
        db.save_checkpoint(conn, args.job_id, "content", {**cp, "warnings": warnings})
        if j["status"] not in ("failed", "canceled"):
            db.set_status(conn, args.job_id, "canceled")
        db.clear_checkpoint(conn, args.job_id, ("draft", "approval"))
        db.set_status(conn, args.job_id, "queued")
        print(f"작업 {args.job_id}번: 화면만 다시 만들도록 대기열에 넣었습니다(글 내용 유지, 새 승인 요청 예정)")
    elif args.cmd == "cancel":
        db.set_status(conn, args.job_id, "canceled")
        print(f"작업 {args.job_id}번을 취소했습니다")
    elif args.cmd in ("once", "run"):
        from src.orchestrator import Orchestrator, run_forever, single_instance
        from src.pipeline import Services
        services = Services.from_settings(settings)
        if args.cmd == "once":
            with single_instance(settings.data_dir / ".worker.lock"):   # 상시 실행기와 동시에 돌지 않게
                done = Orchestrator(conn, services).run_once()
            print("처리한 작업:", done if done else "없음")
        else:
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            run_forever(services, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
