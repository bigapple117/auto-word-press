"""전체 자동 실행기: 장부(db)의 작업을 한 건씩 처리하고, 텔레그램 버튼으로 발행·보류·취소한다.

흐름(블로그+쇼츠):
  queued → generating(자료·글) → tts(성우·자막) → media(사진·배경) → rendering(쇼츠 합성
  + 임시 글 + 승인 요청) → awaiting_approval → [발행] publishing → published
블로그만: generating → media(사진 + 임시 글 + 승인 요청) → awaiting_approval
단계 결과는 체크포인트에 저장되어, 다시 실행하면 끝난 단계는 건너뛴다.

안전장치:
- 실행기는 서버에서 하나만(파일 잠금). 시작할 때 멈춰 있던 작업을 recover_interrupted로 되돌림.
- 버튼은 장부에 저장한 '현재 승인 요청 번호(nonce)'와 같을 때만 처리(예전 메시지 버튼 무시).
- 발행은 begin_publishing으로 먼저 선점한 한 번만, 워드프레스 발행도 두 번 눌러도 안전.
- 텔레그램 offset은 결정을 처리한 뒤에 파일로 저장.
"""
from __future__ import annotations

import fcntl
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from src import db
from src.ai_generator import ModelBusyError
from src.content_schema import MasterContent
from src.pipeline import (PipelineError, Services, make_content, make_draft, make_tts, make_video,
                          pick_media, request_approval, sources_from)
from src import topic_service
from src.telegram_notifier import Decision, NextAction, TelegramError, TopicAction
from src.wp_client import WPError
from src.youtube_kit import MAX_REPORTED_NUMBERS, build_upload_kit, numbers_missing_from_post

log = logging.getLogger(__name__)

IDLE_SECONDS = 15
BUSY_WAIT_SECONDS = 15 * 60               # 글 쓰는 모델이 바쁠 때 대기열 전체를 쉬는 시간(풀리면 빨리 잡도록 짧게 고정)
BUSY_GIVE_UP_SECONDS = 12 * 60 * 60        # 한 작업이 처음 바빠진 뒤 이만큼 지나도 계속 바쁘면 실패로 알림
POLL_WAIT_SECONDS = 25
NAMES = {"publish": "발행", "hold": "보류", "cancel": "취소", "register": "주제 등록", "more": "새 후보",
         "next": "다음 글"}


class Orchestrator:
    def __init__(self, conn: sqlite3.Connection, services: Services,
                 clock=time.monotonic) -> None:
        self.conn = conn
        self.svc = services
        self.clock = clock
        self.busy_until = 0.0                     # 모델이 바빠 쉬는 중이면 이 시각까지 새 작업을 안 가져감
        self._busy_notified = False
        self._busy_since: dict[int, float] = {}   # 작업 번호 → 처음 바빴던 시각(재시작하면 초기화)
        self._pending_requeue: dict[int, tuple[dict[str, Any], Exception, str]] = {}  # 대기열 되돌리기 기록에 실패한 작업 → 다음 작업 전에 다시
        self._unrecorded: dict[int, str] = {}     # 실패 기록에 실패한 작업 → 다음 작업 전에 다시 기록
        self._next_handled: set[int] = set()      # 이미 처리한 [다음 글 보기] 버튼의 메시지 번호

    # ---------- 작업 처리 ----------
    def _advance(self, job_id: int, status: str) -> None:
        """다음 상태로. 체크포인트로 건너뛸 때도 상태는 순서대로 지나간다."""
        if db.get_job(self.conn, job_id)["status"] != status:
            db.set_status(self.conn, job_id, status)

    def process(self, job: dict[str, Any]) -> None:
        """claim_next로 가져온 작업 한 건(상태 generating)을 승인 대기까지 진행한다."""
        job_id, mode, seconds = job["id"], job["mode"], job["target_seconds"]
        if mode == "shorts":
            raise PipelineError("'쇼츠만' 작업은 아직 지원하지 않습니다(유튜브 업로드 연결 후 지원)")
        with_shorts = mode == "blog_shorts"
        cp = job["checkpoint"]

        if "content" not in cp:
            cp["content"] = make_content(self.svc, job["topic"], seconds)
            db.save_checkpoint(self.conn, job_id, "content", cp["content"])
            db.update_fields(self.conn, job_id, title=cp["content"]["content"]["title"])
            if cp["content"].get("fallback_model"):
                self._notify(f"[작업 {job_id}] 1순위 모델이 한도 초과·혼잡이라 예비 모델({cp['content']['model']})로 글을 썼습니다. "
                             "승인 요청이 오면 품질을 꼭 확인해 주세요. 마음에 안 들면 취소 후 다시 쓰기를 요청하세요.")
        content = MasterContent.model_validate(cp["content"]["content"])
        sources = sources_from(cp["content"]["sources"])

        if with_shorts:
            self._advance(job_id, "tts")
            if "tts" not in cp:
                cp["tts"] = make_tts(self.svc, job_id, content, seconds)
                db.save_checkpoint(self.conn, job_id, "tts", cp["tts"])

        self._advance(job_id, "media")
        if "media" not in cp:
            cp["media"] = pick_media(self.svc, content, with_shorts,
                                     avoid=db.used_blog_media_ids(self.conn, exclude_job_id=job_id))
            db.save_checkpoint(self.conn, job_id, "media", cp["media"])

        video_path = None
        if with_shorts:
            self._advance(job_id, "rendering")
            if "video" not in cp:
                cp["video"] = make_video(self.svc, job_id, content, cp["tts"], cp["media"])
                db.save_checkpoint(self.conn, job_id, "video", cp["video"])
                db.update_fields(self.conn, job_id, video_path=cp["video"]["path"])
            video_path = Path(cp["video"]["path"])

        current = db.get_job(self.conn, job_id)
        if "draft" not in cp:
            cp["draft"] = make_draft(self.svc, job_id, content, sources, cp["media"], current["post_id"])
            db.save_checkpoint(self.conn, job_id, "draft", cp["draft"])
        post_id = cp["draft"]["post_id"]
        db.record_post(self.conn, job_id, post_id, cp["draft"]["link"])

        # 승인 요청은 매번 새로 보낸다(재시작 후 다시 보내도 새 요청 번호만 유효)
        draft = cp["draft"]
        approval = request_approval(self.svc, job_id, content, self.approval_warnings(cp, content, post_id),
                                    post_id, video_path, category=draft.get("category", ""))
        db.save_checkpoint(self.conn, job_id, "approval", approval)
        db.set_status(self.conn, job_id, "awaiting_approval")
        log.info("작업 %s번 승인 대기(글 %s번)", job_id, post_id)

    def run_once(self) -> int | None:
        """대기 작업 하나를 처리한다. 처리한 작업 번호(없으면 None)."""
        for job_id, message in list(self._unrecorded.items()):
            db.set_status(self.conn, job_id, "failed", error=message)   # 실패하면 예외 → 루프가 잠시 뒤 재시도
            del self._unrecorded[job_id]
        for job_id, (job, exc, note) in list(self._pending_requeue.items()):
            requeued = db.requeue_busy(self.conn, job_id, note)         # 실패하면 예외 → 루프가 잠시 뒤 재시도
            del self._pending_requeue[job_id]
            if not requeued:                                             # 글 쓰는 단계가 아니었음 → 보통 실패로
                self._record_failure(job, exc)
        if self.clock() < self.busy_until:
            return None
        job = db.claim_next(self.conn)
        if job is None:
            return None
        try:
            self.process(job)
            self._busy_notified = False
            self._busy_since.pop(job["id"], None)
        except ModelBusyError as exc:
            if not self._requeue_busy(job, exc):
                self._record_failure(job, exc)
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 장부에 남기고 알린다
            self._record_failure(job, exc)
        return job["id"]

    def _record_failure(self, job: dict[str, Any], exc: Exception) -> None:
        log.error("작업 %s번 실패", job["id"], exc_info=exc)
        self._busy_since.pop(job["id"], None)    # 바쁨 기록 정리: 다시 시도하면 처음부터, 다음 작업은 새로 안내
        self._busy_notified = False
        message = f"{exc.__class__.__name__}: {exc}"[:500]
        try:
            db.set_status(self.conn, job["id"], "failed", error=message)
        except Exception:  # noqa: BLE001 - 다음 작업을 가져오기 전에 다시 기록한다
            log.exception("작업 %s번 실패 기록 실패(다음 차례에 다시 기록)", job["id"])
            self._unrecorded[job["id"]] = message
        self._notify(f"[작업 {job['id']}] 실패: {job['topic']}\n{message}\n다시 하려면: jobctl retry {job['id']}")

    def _requeue_busy(self, job: dict[str, Any], exc: Exception) -> bool:
        """글 쓰는 모델이 바쁘면 실패로 끝내지 않고 대기열로 되돌린 뒤 잠시 새 작업을 쉰다.
        쉬는 시간은 BUSY_WAIT_SECONDS로 고정(우리 요청량은 구글 혼잡에 영향이 없어 늘릴 이유가 없음).
        처음 바빠진 뒤 BUSY_GIVE_UP_SECONDS가 지나도 계속 바쁘면 실패로 기록·알림하고 다음 작업으로 넘어간다.
        글 쓰는 단계(generating)가 아니면 False → 보통 실패로 기록한다."""
        now = self.clock()
        first = self._busy_since.setdefault(job["id"], now)
        if now - first >= BUSY_GIVE_UP_SECONDS:
            hours = BUSY_GIVE_UP_SECONDS // 3600
            self._record_failure(job, ModelBusyError(f"{hours}시간 넘게 Gemini가 계속 붐벼 중단했습니다. {exc}"))
            return True
        wait = BUSY_WAIT_SECONDS
        minutes = wait // 60
        note = f"모델 바쁨, {minutes}분 뒤 자동 재시도"
        try:
            if not db.requeue_busy(self.conn, job["id"], note):
                return False
        except Exception:  # noqa: BLE001 - 다음 작업을 가져오기 전에 다시 되돌린다
            log.exception("작업 %s번 대기열 되돌리기 실패(다음 차례에 다시)", job["id"])
            self._pending_requeue[job["id"]] = (job, exc, note)
        log.warning("작업 %s번: 글 쓰는 모델이 바빠 %s분 뒤 다시 시도(%s)", job["id"], minutes, exc)
        self.busy_until = now + wait
        if not self._busy_notified:
            self._busy_notified = True
            self._notify(f"[작업 {job['id']}] 구글 Gemini가 붐벼서 글을 못 썼습니다. {minutes}분마다 "
                         f"자동으로 다시 시도합니다(예비 모델로 대충 쓰지 않음). {BUSY_GIVE_UP_SECONDS // 3600}시간 넘게 "
                         "계속 붐비면 실패로 알려 드립니다. 따로 하실 일은 없습니다.")
        return True

    def _notify(self, text: str) -> None:
        try:
            self.svc.telegram.send_text(text)
        except Exception as exc:  # noqa: BLE001
            log.warning("텔레그램 알림 실패: %s", exc)

    # ---------- 버튼 처리 ----------
    def handle_decision(self, d: Decision) -> str:
        """버튼 하나를 처리하고, 사용자 화면에 띄울 짧은 안내를 돌려준다."""
        tg = self.svc.telegram
        job = db.get_job(self.conn, d.job_id)
        approval = (job or {}).get("checkpoint", {}).get("approval") or {}
        if not job or approval.get("nonce") != d.nonce:
            return "지난 승인 요청입니다. 가장 최근 메시지의 버튼을 눌러 주세요"
        if job["status"] == "published":
            return "이미 발행된 글입니다"
        if d.action == "hold":
            self.offer_next(d.job_id, f"[작업 {d.job_id}] 보류했습니다. 나중에 위 메시지의 [발행]을 눌러도 됩니다.")
            return "보류했습니다. 나중에 [발행]을 눌러도 됩니다"
        if d.action == "cancel":
            # 요청 번호 확인과 취소를 장부에서 한 번에(그사이 새 승인 요청이 끼어들어도 안전)
            if not db.cancel_if_current(self.conn, d.job_id, d.nonce):
                return "취소할 수 없는 상태이거나 지난 요청입니다"
            tg.mark_done(d.message_id, f"[작업 {d.job_id}] 취소했습니다. 워드프레스 임시 글은 그대로 남겨 두었습니다.")
            self.offer_next(d.job_id, "다음 승인 대기 글로 넘어가려면 아래 버튼을 누르세요.")
            return "취소했습니다"
        # 발행: 요청 번호 확인과 선점을 장부에서 한 번에
        if not db.begin_publishing(self.conn, d.job_id, nonce=d.nonce):
            return "이미 처리 중이거나 발행할 수 없는 상태입니다"
        post_id = db.get_job(self.conn, d.job_id)["post_id"]
        try:
            post = self.svc.wp.publish(d.job_id, post_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("작업 %s번 발행 요청 실패", d.job_id)
            definite = isinstance(exc, WPError) and exc.status is not None and 400 <= exc.status < 500
            if definite:   # 워드프레스가 분명히 거절 → 승인 대기로 되돌려 다시 누를 수 있게
                db.set_status(self.conn, d.job_id, "awaiting_approval")
                self._notify(f"[작업 {d.job_id}] 발행 실패: {exc}\n문제를 고친 뒤 같은 메시지의 [발행]을 다시 누르세요.")
                return "발행에 실패했습니다"
            # 응답이 끊기는 등 결과가 불확실 → '발행 중'으로 두고 실제 상태를 확인해 맞춘다
            self._notify(f"[작업 {d.job_id}] 발행 결과를 확인하지 못했습니다({exc.__class__.__name__}). "
                         "몇 분 안에 워드프레스 실제 상태를 확인해 정리합니다.")
            return "발행 결과 확인 중입니다"
        try:
            db.mark_published(self.conn, d.job_id, post.id, post.link)
            recorded = True
        except Exception:  # noqa: BLE001 - 발행은 됐으니 장부는 reconcile_publishing이 나중에 맞춘다
            log.exception("작업 %s번: 발행됐지만 장부 기록 실패(나중에 다시 맞춤)", d.job_id)
            recorded = False
        tg.mark_done(d.message_id, f"[작업 {d.job_id}] 발행했습니다.\n{post.link}")
        if recorded:   # 기록 실패면 reconcile_publishing이 장부를 맞추면서 한 번만 보낸다(중복 방지)
            if not self.send_upload_kit(d.job_id, post.link):   # 쇼츠가 없으면 다음 글 버튼만
                self.offer_next(d.job_id, "다음 승인 대기 글로 넘어가려면 아래 버튼을 누르세요.")
        return "발행했습니다"

    def topic_deps(self) -> topic_service.TopicDeps:
        return topic_service.TopicDeps(self.svc.settings, self.svc.search, self.svc.generator, self.svc.telegram)

    def handle_topic(self, a: TopicAction) -> str:
        """주제 후보 버튼([n번 등록]·[새 후보 더 받기])."""
        return topic_service.handle(self.topic_deps(), self.conn, a)

    def approval_warnings(self, cp: dict[str, Any], content: MasterContent, post_id: int) -> list[str]:
        draft = cp.get("draft") or {}
        warnings = list(cp["content"]["warnings"]) + ([draft["category_warning"]] if draft.get("category_warning") else [])
        if "video" in cp:
            mismatch = self.shorts_number_check(content, post_id)
            if mismatch:
                warnings.append(f"쇼츠·유튜브 설명의 숫자 중 글 본문에 없는 것(영상 확인): {mismatch}")
        return warnings

    # ---------- 다음 승인 대기 글 ----------
    def awaiting_jobs(self) -> list[dict[str, Any]]:
        return sorted(db.list_jobs(self.conn, "awaiting_approval", limit=-1), key=lambda j: j["id"])   # -1: 개수 제한 없음

    def next_markup(self, after_job_id: int) -> dict | None:
        """승인 대기 글이 남아 있으면 [다음 승인 대기 글 보기] 버튼, 없으면 None."""
        remaining = len(self.awaiting_jobs())
        return self.svc.telegram.next_markup(after_job_id, remaining) if remaining else None

    def offer_next(self, after_job_id: int, text: str) -> None:
        """다음 글로 넘어가는 버튼을 맨 아래에 보낸다(승인 대기 글이 없으면 보내지 않음). 실패해도 무시."""
        try:
            markup = self.next_markup(after_job_id)
            if markup:
                self.svc.telegram.send_text(text, reply_markup=markup)
        except Exception as exc:  # noqa: BLE001
            log.warning("다음 글 버튼 전송 실패: %s", exc)

    def handle_next(self, a: NextAction) -> str:
        """after_job_id 다음 번호의 승인 대기 글(끝이면 처음으로)을 맨 아래로 다시 보낸다.
        보내는 데 1분쯤 걸려 같은 버튼을 여러 번 누를 수 있으므로, 한 메시지의 버튼은 한 번만 처리하고 바로 지운다."""
        if a.message_id is not None:
            if a.message_id in self._next_handled:
                return "이미 처리한 버튼입니다. 맨 아래 메시지를 확인해 주세요"
            self._next_handled.add(a.message_id)
            self.svc.telegram.clear_buttons(a.message_id)
        try:
            jobs = self.awaiting_jobs()
            if not jobs:
                self.svc.telegram.send_text("승인 대기 중인 글이 없습니다.")
                return "승인 대기 글이 없습니다"
            job = next((j for j in jobs if j["id"] > a.after_job_id), jobs[0])
            try:
                self.svc.telegram.answer(a.callback_id, f"작업 {job['id']}번을 아래로 보내는 중입니다(1분쯤)")
            except Exception:  # noqa: BLE001 - 안내 실패가 다시 보내기를 막지 않게
                pass
            self.resend_approval(job)
            return f"작업 {job['id']}번을 아래로 보냈습니다"
        except Exception as exc:  # noqa: BLE001 - 실패하면 다시 누를 수 있는 버튼을 새로 준다
            log.exception("다음 글 보내기 실패")
            self.offer_next(a.after_job_id, f"다음 글을 보내지 못했습니다({exc.__class__.__name__}). "
                                            "아래 버튼으로 다시 시도해 주세요.")
            return "다음 글을 보내지 못했습니다"

    def resend_approval(self, job: dict[str, Any]) -> None:
        """승인 요청(화면 캡처 새로 찍기·쇼츠·버튼)을 다시 보내고 새 요청 번호로 바꾼다. 예전 메시지 버튼은 지운다."""
        cp = job["checkpoint"]
        content = MasterContent.model_validate(cp["content"]["content"])
        post_id = cp["draft"]["post_id"]
        video = (cp.get("video") or {}).get("path")
        video_path = Path(video) if video and Path(video).exists() else None
        old_message = (cp.get("approval") or {}).get("message_id")
        approval = request_approval(self.svc, job["id"], content, self.approval_warnings(cp, content, post_id),
                                    post_id, video_path, category=cp["draft"].get("category", ""))
        try:
            db.save_checkpoint(self.conn, job["id"], "approval", approval)
        except Exception:
            # 장부에는 예전 요청 번호가 남아 새 메시지 버튼이 안 먹으므로, 새 메시지 버튼을 지우고 실패로 알린다
            self.svc.telegram.clear_buttons(approval.get("message_id"))
            raise
        self.svc.telegram.clear_buttons(old_message)

    def shorts_number_check(self, content: MasterContent, post_id: int | None) -> str:
        """쇼츠·설명에 쓰는 숫자 중 지금 글 본문에 없는 것. 확인 자체가 실패하면 빈 문자열(알림·발행은 계속)."""
        if not post_id:
            return ""
        try:
            missing = numbers_missing_from_post(content, self.svc.wp.get_post_content(post_id))
        except Exception as exc:  # noqa: BLE001 - 보조 확인이라 실패해도 흐름을 막지 않는다
            log.warning("글 %s번 쇼츠 숫자 대조 실패: %s", post_id, exc)
            return ""
        more = len(missing) - MAX_REPORTED_NUMBERS
        return ", ".join(missing[:MAX_REPORTED_NUMBERS]) + (f" 외 {more}개" if more > 0 else "")

    def send_upload_kit(self, job_id: int, post_url: str) -> bool:
        """쇼츠가 있는 작업이면 유튜브에 올릴 영상·제목·설명을 텔레그램으로 보낸다.
        끝까지 보냈으면 True(설명 메시지에 다음 글 버튼 포함), 쇼츠가 없거나 실패하면 False.
        제목과 설명은 따로 보내 휴대폰에서 메시지 하나씩 길게 눌러 복사할 수 있게 한다.
        실패해도 발행 결과에는 영향이 없다(알림만 남김)."""
        tg = self.svc.telegram
        try:
            job = db.get_job(self.conn, job_id) or {}
            video = (job.get("checkpoint", {}).get("video") or {}).get("path")
            if not video or not Path(video).exists():
                return False
            content = MasterContent.model_validate(job["checkpoint"]["content"]["content"])
            kit = build_upload_kit(content, post_url)
            # 승인 전에 글을 직접 고쳤으면 영상은 옛 내용 그대로라, 영상보다 먼저 확인 요청을 보낸다
            mismatch = self.shorts_number_check(content, job.get("post_id"))
            if mismatch:
                try:   # 확인 요청을 못 보내도 아래 영상·제목·설명은 보낸다
                    tg.send_text(f"[작업 {job_id}] 올리기 전 확인 필요\n"
                                 f"쇼츠 영상·설명의 숫자 중 지금 글에 없는 것이 있습니다: {mismatch}\n"
                                 "글을 고친 뒤 영상이 옛 내용 그대로이거나, 표기만 다른 같은 값일 수 있습니다. "
                                 "영상에서 이 숫자가 맞는지 확인하고 올려 주세요.")
                except Exception as exc:  # noqa: BLE001
                    log.warning("작업 %s번 쇼츠 숫자 확인 요청 전송 실패: %s", job_id, exc)
            tg.send_video(Path(video), f"[작업 {job_id}] 유튜브 쇼츠 업로드용 영상\n"
                                       "아래 두 메시지(제목, 설명)를 길게 눌러 복사해 붙여 넣으세요.")
            tg.send_text(kit.title)
            try:
                markup = self.next_markup(job_id)
            except Exception as exc:  # noqa: BLE001 - 버튼이 없어도 설명은 보낸다
                log.warning("다음 글 버튼 만들기 실패: %s", exc)
                markup = None
            tg.send_text(kit.description, reply_markup=markup)
            return True
        except Exception as exc:  # noqa: BLE001 - 발행은 끝났으니 업로드 안내 실패는 알리기만
            log.exception("작업 %s번 유튜브 업로드 안내 실패", job_id)
            self._notify(f"[작업 {job_id}] 유튜브 업로드용 제목·설명을 보내지 못했습니다({exc.__class__.__name__}). "
                         "영상은 승인 요청 메시지에 있습니다.")
            return False      # 안내가 끊겼으면 다음 글 버튼은 따로 보낸다

    def reconcile_publishing(self, older_than_seconds: int = 180) -> list[int]:
        """'발행 중'으로 오래 멈춘 작업을 워드프레스 실제 상태와 맞춘다(버튼 수신 스레드에서 주기적으로)."""
        fixed = []
        for job in db.stale_publishing(self.conn, older_than_seconds):
            try:
                post = self.svc.wp.get_post(job["post_id"])
                if post and post.status == "publish":
                    db.mark_published(self.conn, job["id"], post.id, post.link)
                    if not self.send_upload_kit(job["id"], post.link):
                        self.offer_next(job["id"], "다음 승인 대기 글로 넘어가려면 아래 버튼을 누르세요.")
                else:
                    db.set_status(self.conn, job["id"], "awaiting_approval")
                fixed.append(job["id"])
            except Exception:  # noqa: BLE001
                log.exception("작업 %s번 발행 상태 맞추기 실패", job["id"])
        return fixed

    def poll_once(self, offset: int | None, wait: int = POLL_WAIT_SECONDS) -> int | None:
        decisions, next_offset = self.svc.telegram.poll_decisions(offset, wait_seconds=wait)
        for d in decisions:
            try:
                if isinstance(d, NextAction):
                    note = self.handle_next(d)
                elif isinstance(d, TopicAction):
                    note = self.handle_topic(d)
                else:
                    note = self.handle_decision(d)
            except Exception as exc:  # noqa: BLE001 - 버튼 하나의 실패가 수신을 멈추지 않게
                log.exception("버튼 처리 실패")
                note = f"처리 중 오류: {exc.__class__.__name__}"
            try:   # 화면 안내 실패가 이미 처리한 결정의 offset 저장을 막지 않게
                self.svc.telegram.answer(d.callback_id, f"[{NAMES.get(d.action, d.action)}] {note}")
            except Exception as exc:  # noqa: BLE001
                log.warning("버튼 화면 안내 실패: %s", exc)
        return next_offset


# ---------- 상시 실행 ----------

def _load_offset(path: Path) -> int | None:
    try:
        return int(json.loads(path.read_text())["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_offset(path: Path, offset: int | None) -> None:
    if offset is None:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"offset": offset}))
    tmp.replace(path)


@contextmanager
def single_instance(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("실행기가 이미 돌고 있습니다(중복 실행 방지)") from None
        yield


def run_forever(services: Services, stop: threading.Event | None = None) -> None:
    """작업 처리(본 스레드)와 버튼 수신(별도 스레드)을 함께 돌린다. Ctrl+C·종료 신호로 멈춘다."""
    stop = stop or threading.Event()
    settings = services.settings
    with single_instance(settings.data_dir / ".worker.lock"):
        main_conn = db.connect(settings.db_path)
        recovered = db.recover_interrupted(main_conn)
        if recovered:
            log.info("멈춰 있던 작업 되돌림: %s", recovered)
        offset_file = settings.data_dir / "telegram_offset.json"

        def listen() -> None:
            conn = db.connect(settings.db_path)
            orch = Orchestrator(conn, services)
            offset = _load_offset(offset_file)
            while not stop.is_set():
                try:
                    orch.reconcile_publishing()
                    new = orch.poll_once(offset)
                    if new != offset:
                        _save_offset(offset_file, new)    # 결정을 처리한 뒤에 저장
                        offset = new
                except TelegramError as exc:
                    log.warning("텔레그램 수신 오류: %s", exc)
                    stop.wait(10)
                except Exception:  # noqa: BLE001
                    log.exception("버튼 수신 스레드 오류")
                    stop.wait(10)

        listener = threading.Thread(target=listen, name="telegram-listener", daemon=True)
        listener.start()

        def weekly_topics() -> None:
            """주 1회 블로그 주제 후보(월요일 오전, 한국 시간). 실패해도 글 작성·승인 버튼과 무관."""
            deps = topic_service.TopicDeps(settings, services.search, services.generator, services.telegram)
            while not stop.wait(topic_service.CHECK_SECONDS):
                try:
                    note = topic_service.run_weekly_if_due(deps)
                    if note:
                        log.info("주간 주제 후보: %s", note)
                except Exception:  # noqa: BLE001
                    log.exception("주간 주제 후보 확인 오류")

        threading.Thread(target=weekly_topics, name="topic-weekly", daemon=True).start()
        worker = Orchestrator(main_conn, services)
        log.info("실행기 시작")
        try:
            while not stop.is_set():
                try:
                    if worker.run_once() is None:
                        stop.wait(IDLE_SECONDS)
                except Exception:  # noqa: BLE001 - 장부 일시 오류 등: 실행기는 멈추지 않고 잠시 뒤 다시
                    log.exception("작업 처리 루프 오류, 30초 뒤 다시 시도")
                    stop.wait(30)
        except KeyboardInterrupt:
            log.info("종료 요청")
        finally:
            stop.set()
            listener.join(timeout=POLL_WAIT_SECONDS + 20)
            log.info("실행기 종료")
