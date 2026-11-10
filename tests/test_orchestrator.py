from dataclasses import dataclass
from pathlib import Path

import sqlite3
import pytest

from src import db
from src.ai_generator import GenerationResult
from src.config import Settings
from src.content_schema import MasterContent
from src.orchestrator import Orchestrator
from src.pipeline import Services
from src.source_fetcher import Source
from src.telegram_notifier import ApprovalRequest, Decision
from src.wp_client import WPMedia, WPPost
from tests.test_content import good_data


@dataclass
class Hit:
    published: str
    title: str
    url: str


class FakeSearch:
    def news(self, topic, display=5):
        return [Hit("Mon, 02 Nov 2026 07:50", "근로장려금 기한 후 신청", "https://news/1")]


class FakeGenerator:
    def __init__(self):
        self.calls = 0

    model = "gemini-3.8-flash"

    def generate(self, topic, sources, seconds, news=()):
        self.calls += 1
        return GenerationResult(MasterContent.model_validate(good_data()), self.model, 1, ["경고 하나"])

    category = "tax"

    def choose_category(self, title, summary, tags, options):
        self.category_options = list(options)
        if isinstance(self.category, Exception):
            raise self.category
        return self.category


@dataclass
class Pick:
    path: Path
    kind: str = "image"
    media_id: int = 1
    credit: str = "u / Pixabay"
    page_url: str = "https://pixabay.com/p/1"


class FakePicker:
    def __init__(self, tmp):
        self.tmp = tmp

    def _file(self, name):
        from PIL import Image
        p = self.tmp / name
        if not p.exists():
            Image.new("RGB", (1600, 1000), "blue").save(p)
        return p

    def pick_blog_images(self, queries, avoid=()):
        return [Pick(self._file(f"b{i}.jpg"), media_id=i) if q else None for i, q in enumerate(queries)]

    def pick_blog_image(self, query, avoid=()):
        return Pick(self._file("extra.jpg"), media_id=99)

    def pick_scene_backgrounds(self, queries):
        return [None for _ in queries]


class FakeWP:
    def __init__(self):
        self.drafts = {}
        self.published = []
        self.fail_publish = 0
        self.uploads = []

    def get_post_content(self, post_id):
        if getattr(self, "fail_content", False):
            raise RuntimeError("본문 조회 실패 시험")
        if post_id in getattr(self, "edited", {}):              # 관리자 화면에서 직접 고친 본문
            return self.edited[post_id]
        return next(html for pid, html in self.drafts.values() if pid == post_id)

    def upload_media(self, path, job_id, index, alt="", caption=""):
        self.uploads.append((job_id, index))
        return WPMedia(100 + index, f"https://jalcheck.com/wp-content/uploads/jcjob-{job_id}-{index}.jpg")

    def ensure_tags(self, names):
        self.tag_names = list(names)
        return [900 + i for i, _ in enumerate(names)]

    def list_categories(self):
        return [{"id": 1, "slug": "benefits", "name": "지원금·복지", "description": "받는 지원금"},
                {"id": 48, "slug": "tax", "name": "세금·절세", "description": "내고 돌려받는 세금"}]

    def save_draft(self, job_id, title, html, slug="", excerpt="", featured_media=None, post_id=None, tags=None,
                   categories=None):
        self.last_tags = tags
        self.last_categories = categories
        pid = post_id or self.drafts.get(job_id, (500 + job_id,))[0]
        self.drafts[job_id] = (pid, html)
        return WPPost(pid, "draft", f"https://jalcheck.com/?p={pid}")

    def publish(self, job_id, post_id):
        if self.fail_publish:
            self.fail_publish -= 1
            from src.wp_client import WPError
            raise WPError("워드프레스 오류 403", 403)
        if getattr(self, "timeout_publish", 0):
            self.timeout_publish -= 1
            self.published.append((job_id, post_id))     # 실제로는 발행됐지만 응답이 끊김
            raise ConnectionError("응답 끊김")
        self.published.append((job_id, post_id))
        return WPPost(post_id, "publish", f"https://jalcheck.com/post-{post_id}/")


class FakeTG:
    def __init__(self):
        self.requests = []
        self.texts = []
        self.done = []
        self.counter = 0

    def send_approval_request(self, job_id, title, summary, edit_url, warnings=(), video_path=None, screenshots=(),
                              category=""):
        if getattr(self, "fail_request", 0):
            self.fail_request -= 1
            raise RuntimeError("승인 요청 전송 실패 시험")
        self.counter += 1
        self.last_category = category
        req = ApprovalRequest(self.counter, f"{self.counter:08x}")
        self.requests.append((job_id, req, list(warnings), video_path, list(screenshots), edit_url))
        return req

    def send_text(self, text):
        self.texts.append(text)

    def send_video(self, path, caption=""):
        if getattr(self, "fail_video", False):
            raise RuntimeError("영상 전송 실패 시험")
        self.videos = getattr(self, "videos", []) + [(Path(path).name, caption)]

    def mark_done(self, message_id, note):
        self.done.append((message_id, note))

    def answer(self, callback_id, text):
        pass


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", wp_base_url="https://jalcheck.com")
    calls = {"render": 0, "tts": 0}

    def synthesize(narrations, out, voice, ffmpeg, target_seconds=None):
        calls["tts"] += 1
        out.mkdir(parents=True, exist_ok=True)
        from types import SimpleNamespace
        return SimpleNamespace(audio_path=out / "n.wav", timeline_path=out / "t.json", duration=30.0, rate=0)

    def render(timeline, audio, media, out, ffmpeg, ffprobe, fonts, title=None):
        calls["render"] += 1
        if calls.get("render_fail"):
            calls["render_fail"] -= 1
            raise RuntimeError("렌더링 실패 시험")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        from types import SimpleNamespace
        return SimpleNamespace(path=out, duration=30.6, seconds_taken=1.0)

    def fetch(urls, max_sources=4, fallback=None):
        return [Source("S1", "https://www.nts.go.kr/a", "근로장려금 소개", "자료1 " * 50),
                Source("S2", "https://www.nts.go.kr/b", "신청자격", "자료2 " * 50)], []

    shots = []
    svc = Services(settings=settings, search=FakeSearch(), reader=None, generator=FakeGenerator(),
                   picker=FakePicker(tmp_path), wp=FakeWP(), telegram=FakeTG(), synthesize=synthesize,
                   render=render, fetch=fetch, find_urls=lambda search, topic: ["https://www.nts.go.kr/a"],
                   screenshot=lambda pid, out: shots.append(pid) or [out / f"post{pid}_mobile.png"])
    conn = db.connect(settings.db_path)
    return Orchestrator(conn, svc), svc, conn, calls


def decision(job_id, action, nonce, msg=1):
    return Decision(job_id, action, nonce, f"cb-{action}", msg)


def test_full_flow_to_approval_then_publish_once(env):
    orch, svc, conn, calls = env
    job_id = db.create_job(conn, "근로장려금 신청 자격")
    assert orch.run_once() == job_id
    job = db.get_job(conn, job_id)
    assert job["status"] == "awaiting_approval" and job["post_id"] == 500 + job_id
    assert set(job["checkpoint"]) >= {"content", "tts", "media", "video", "draft", "approval"}
    assert job["title"] == good_data()["title"] and job["video_path"].endswith("short.mp4")
    (jid, req, warnings, video, shots, edit_url) = svc.telegram.requests[0]
    assert jid == job_id and "경고 하나" in warnings and video.name == "short.mp4" and shots
    assert edit_url.endswith(f"post={500 + job_id}&action=edit")
    assert svc.wp.uploads[0] == (job_id, 0)                         # 첫 사진(대표 사진)

    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    assert db.get_job(conn, job_id)["status"] == "published"
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "이미 발행된 글입니다"
    assert svc.wp.published == [(job_id, 500 + job_id)]
    assert "발행했습니다" in svc.telegram.done[-1][1]


def test_old_or_forged_nonce_is_ignored(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    assert "지난 승인 요청" in orch.handle_decision(decision(job_id, "publish", "deadbeef"))
    assert "지난 승인 요청" in orch.handle_decision(decision(999, "publish", "deadbeef"))
    assert svc.wp.published == []


def test_hold_then_publish_and_cancel(env):
    orch, svc, conn, _ = env
    a = db.create_job(conn, "A")
    b = db.create_job(conn, "B")
    orch.run_once()
    orch.run_once()
    na = db.get_job(conn, a)["checkpoint"]["approval"]["nonce"]
    nb = db.get_job(conn, b)["checkpoint"]["approval"]["nonce"]
    assert "보류" in orch.handle_decision(decision(a, "hold", na))
    assert db.get_job(conn, a)["status"] == "awaiting_approval"
    assert orch.handle_decision(decision(a, "publish", na)) == "발행했습니다"
    assert orch.handle_decision(decision(b, "cancel", nb)) == "취소했습니다"
    assert db.get_job(conn, b)["status"] == "canceled"
    assert "발행할 수 없는" in orch.handle_decision(decision(b, "publish", nb))


def test_failure_is_recorded_and_retry_skips_finished_steps(env):
    orch, svc, conn, calls = env
    calls["render_fail"] = 1
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    job = db.get_job(conn, job_id)
    assert job["status"] == "failed" and "렌더링 실패 시험" in job["error"]
    assert "실패" in svc.telegram.texts[-1] and f"retry {job_id}" in svc.telegram.texts[-1]
    db.set_status(conn, job_id, "queued")
    orch.run_once()
    assert db.get_job(conn, job_id)["status"] == "awaiting_approval"
    assert svc.generator.calls == 1 and calls["tts"] == 1             # 글·성우는 다시 만들지 않음


def test_publish_failure_returns_to_approval_and_can_retry(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    nonce = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    svc.wp.fail_publish = 1
    assert "실패" in orch.handle_decision(decision(job_id, "publish", nonce))
    assert db.get_job(conn, job_id)["status"] == "awaiting_approval"
    assert orch.handle_decision(decision(job_id, "publish", nonce)) == "발행했습니다"


def test_blog_only_skips_shorts(env):
    orch, svc, conn, calls = env
    job_id = db.create_job(conn, "주제", mode="blog")
    orch.run_once()
    job = db.get_job(conn, job_id)
    assert job["status"] == "awaiting_approval" and "tts" not in job["checkpoint"] and "video" not in job["checkpoint"]
    assert calls["tts"] == 0 and calls["render"] == 0 and svc.telegram.requests[0][3] is None


def test_restart_after_crash_sends_new_approval_and_old_buttons_stop_working(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    old = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    # 승인 요청을 보내는 도중 멈췄다고 가정: 상태를 렌더링 단계로 되돌린 뒤 재시작 복구
    conn.execute("UPDATE jobs SET status='rendering' WHERE id=?", (job_id,))
    assert db.recover_interrupted(conn) == [job_id]
    orch.run_once()
    new = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    assert new != old and len(svc.wp.drafts) == 1                     # 임시 글은 그대로 하나
    assert "지난 승인 요청" in orch.handle_decision(decision(job_id, "publish", old))
    assert orch.handle_decision(decision(job_id, "publish", new)) == "발행했습니다"


def test_shorts_only_mode_fails_clearly(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제", mode="shorts")
    orch.run_once()
    assert "아직 지원하지 않습니다" in db.get_job(conn, job_id)["error"]


def test_db_nonce_checks_are_atomic(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    nonce = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    assert db.begin_publishing(conn, job_id, nonce="00000000") is False      # 예전 요청 번호
    assert db.cancel_if_current(conn, job_id, "00000000") is False
    assert db.get_job(conn, job_id)["status"] == "awaiting_approval"
    assert db.cancel_if_current(conn, job_id, nonce) is True
    assert db.get_job(conn, job_id)["status"] == "canceled"


def test_ledger_failure_after_publish_is_reconciled(env, monkeypatch):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    nonce = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    real = db.mark_published
    monkeypatch.setattr(db, "mark_published", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db busy")))
    assert orch.handle_decision(decision(job_id, "publish", nonce)) == "발행했습니다"
    assert db.get_job(conn, job_id)["status"] == "publishing"
    monkeypatch.setattr(db, "mark_published", real)
    svc.wp.get_post = lambda pid: WPPost(pid, "publish", f"https://jalcheck.com/post-{pid}/")
    assert orch.reconcile_publishing(older_than_seconds=0) == [job_id]
    assert db.get_job(conn, job_id)["status"] == "published"


def test_stale_publishing_not_published_goes_back_to_approval(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    nonce = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    db.begin_publishing(conn, job_id, nonce=nonce)
    svc.wp.get_post = lambda pid: WPPost(pid, "draft", "")
    assert orch.reconcile_publishing(older_than_seconds=0) == [job_id]
    assert db.get_job(conn, job_id)["status"] == "awaiting_approval"


def test_answer_failure_does_not_block_offset(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    nonce = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    svc.telegram.poll_decisions = lambda offset, wait_seconds=25: ([decision(job_id, "hold", nonce),
                                                                    decision(job_id, "publish", nonce)], 42)

    def boom(*a):
        raise RuntimeError("answer 실패")

    svc.telegram.answer = boom
    assert orch.poll_once(None) == 42
    assert db.get_job(conn, job_id)["status"] == "published"


def test_run_once_survives_failure_recording_error(env, monkeypatch):
    orch, svc, conn, calls = env
    calls["render_fail"] = 1
    db.create_job(conn, "주제")
    real = db.set_status

    def flaky(c, job_id, status, error=None):
        if status == "failed":
            raise RuntimeError("db locked")
        return real(c, job_id, status, error)

    monkeypatch.setattr(db, "set_status", flaky)
    assert orch.run_once() is not None                                # 예외가 밖으로 새지 않음


def test_uncertain_publish_stays_publishing_and_reconciles(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    nonce = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    svc.wp.timeout_publish = 1
    assert "확인 중" in orch.handle_decision(decision(job_id, "publish", nonce))
    assert db.get_job(conn, job_id)["status"] == "publishing"
    assert "취소할 수 없는" in orch.handle_decision(decision(job_id, "cancel", nonce))   # 공개된 글을 취소로 기록하지 않음
    svc.wp.get_post = lambda pid: WPPost(pid, "publish", f"https://jalcheck.com/post-{pid}/")
    orch.reconcile_publishing(older_than_seconds=0)
    assert db.get_job(conn, job_id)["status"] == "published"


def test_failure_record_retried_before_next_job(env, monkeypatch):
    orch, svc, conn, calls = env
    calls["render_fail"] = 1
    first = db.create_job(conn, "A")
    real = db.set_status
    state = {"fail": True}

    def flaky(c, job_id, status, error=None):
        if status == "failed" and state["fail"]:
            raise RuntimeError("db locked")
        return real(c, job_id, status, error)

    monkeypatch.setattr(db, "set_status", flaky)
    orch.run_once()
    assert db.get_job(conn, first)["status"] == "rendering" and first in orch._unrecorded
    state["fail"] = False
    db.create_job(conn, "B")
    orch.run_once()
    assert db.get_job(conn, first)["status"] == "failed" and not orch._unrecorded


def test_fallback_model_is_flagged_and_notified(env):
    orch, svc, conn, _ = env
    svc.generator.model = "gemini-3.5-flash-lite"
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    assert any("예비 모델" in t for t in svc.telegram.texts)
    assert svc.telegram.requests[0][2][0].startswith("예비 모델(gemini-3.5-flash-lite) 작성")


def test_redo_clears_checkpoints_keeps_post(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    old_post = db.get_job(conn, job_id)["post_id"]
    old_nonce = db.get_job(conn, job_id)["checkpoint"]["approval"]["nonce"]
    db.set_status(conn, job_id, "canceled")
    db.clear_checkpoint(conn, job_id, ("content", "tts", "media", "video", "draft", "approval"))
    db.set_status(conn, job_id, "queued")
    orch.run_once()
    job = db.get_job(conn, job_id)
    assert svc.generator.calls == 2 and job["post_id"] == old_post and job["status"] == "awaiting_approval"
    assert "지난 승인 요청" in orch.handle_decision(decision(job_id, "publish", old_nonce))


def test_draft_gets_tags_editorial_box_and_alt(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    html = svc.wp.drafts[job_id][1]
    assert 'class="jc-editorial"' in html and "자료 확인일:" in html and 'href="/about/"' in html
    assert svc.wp.tag_names[0] == good_data()["focus_keyword"] and svc.wp.last_tags


def test_busy_model_requeues_and_pauses_queue(env):
    from src.ai_generator import ModelBusyError
    from src.orchestrator import BUSY_WAIT_SECONDS
    orch, svc, conn, _ = env
    now = [1000.0]
    orch.clock = lambda: now[0]
    real = svc.generator.generate
    state = {"busy": 2}

    def generate(*a, **kw):
        if state["busy"]:
            state["busy"] -= 1
            raise ModelBusyError("503")
        return real(*a, **kw)

    svc.generator.generate = generate
    first = db.create_job(conn, "A")
    second = db.create_job(conn, "B")
    assert orch.run_once() == first
    job = db.get_job(conn, first)
    assert job["status"] == "queued" and "자동 재시도" in job["error"]
    assert orch.run_once() is None and db.get_job(conn, second)["status"] == "queued"   # 쉬는 중
    now[0] += BUSY_WAIT_SECONDS + 1
    assert orch.run_once() == first and db.get_job(conn, first)["status"] == "queued"  # 또 바쁨
    assert sum("붐벼서" in t for t in svc.telegram.texts) == 1                          # 알림은 한 번만
    now[0] += BUSY_WAIT_SECONDS + 1
    orch.run_once()
    assert db.get_job(conn, first)["status"] == "awaiting_approval"
    assert not any("] 실패:" in t for t in svc.telegram.texts)


def test_busy_gives_up_after_limit_and_moves_on(env):
    from src.ai_generator import ModelBusyError
    from src.orchestrator import BUSY_GIVE_UP_SECONDS, BUSY_WAIT_SECONDS
    orch, svc, conn, _ = env
    now = [1000.0]
    orch.clock = lambda: now[0]
    svc.generator.generate = lambda *a, **k: (_ for _ in ()).throw(ModelBusyError("503"))
    first = db.create_job(conn, "A")
    second = db.create_job(conn, "B")
    seen = []
    while db.get_job(conn, first)["status"] == "queued":
        assert orch.run_once() == first
        seen.append(orch.busy_until - now[0])
        now[0] = max(now[0], orch.busy_until) + 1
    job = db.get_job(conn, first)
    assert job["status"] == "failed" and "12시간" in job["error"]
    assert all(w == BUSY_WAIT_SECONDS for w in seen[:-1])                # 간격은 고정
    assert len(seen) - 1 >= BUSY_GIVE_UP_SECONDS // (BUSY_WAIT_SECONDS + 1)
    assert any("] 실패:" in t and "12시간" in t for t in svc.telegram.texts)
    assert db.get_job(conn, second)["status"] == "queued"
    assert orch.run_once() == second                                  # 바로 다음 작업으로
    assert db.get_job(conn, second)["status"] == "queued" and "15분" in db.get_job(conn, second)["error"]
    assert sum("붐벼서" in t for t in svc.telegram.texts) == 2         # 새 작업도 한 번 안내


def test_busy_requeue_db_failure_is_retried_before_next_claim(env, monkeypatch):
    from src.ai_generator import ModelBusyError
    orch, svc, conn, _ = env
    svc.generator.generate = lambda *a, **k: (_ for _ in ()).throw(ModelBusyError("503"))
    job_id = db.create_job(conn, "A")
    real = db.requeue_busy
    state = {"fail": True}

    def flaky(c, jid, note):
        if state["fail"]:
            raise sqlite3.OperationalError("locked")
        return real(c, jid, note)

    monkeypatch.setattr(db, "requeue_busy", flaky)
    orch.run_once()
    assert db.get_job(conn, job_id)["status"] == "generating" and job_id in orch._pending_requeue
    state["fail"] = False
    orch.run_once()
    assert db.get_job(conn, job_id)["status"] == "queued" and not orch._pending_requeue


def test_requeue_busy_only_from_generating(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "A")
    assert db.requeue_busy(conn, job_id, "x") is False          # queued 상태면 안 바뀜
    db.claim_next(conn)
    assert db.requeue_busy(conn, job_id, "x") is True


def test_publish_sends_youtube_upload_kit(env):
    orch, svc, conn, calls = env
    job_id = db.create_job(conn, "근로장려금 신청 자격")
    orch.run_once()
    req = svc.telegram.requests[0][1]
    svc.telegram.texts.clear()
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    assert svc.telegram.videos[0][0] == "short.mp4" and "업로드용" in svc.telegram.videos[0][1]
    title, desc = svc.telegram.texts
    assert title == good_data()["shorts"]["title"]
    assert f"https://jalcheck.com/post-{500 + job_id}/" in desc and desc.rstrip().endswith("#잘체크")


def test_blog_only_publish_sends_no_upload_kit(env):
    orch, svc, conn, calls = env
    job_id = db.create_job(conn, "주제", mode="blog")
    orch.run_once()
    req = svc.telegram.requests[0][1]
    svc.telegram.texts.clear()
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    assert not getattr(svc.telegram, "videos", []) and svc.telegram.texts == []


def test_upload_kit_failure_does_not_undo_publish(env):
    orch, svc, conn, calls = env
    job_id = db.create_job(conn, "근로장려금 신청 자격")
    orch.run_once()
    req = svc.telegram.requests[0][1]
    svc.telegram.fail_video = True
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    assert db.get_job(conn, job_id)["status"] == "published"
    assert "보내지 못했습니다" in svc.telegram.texts[-1]


def test_upload_kit_sent_once_when_ledger_write_fails(env, monkeypatch):
    orch, svc, conn, calls = env
    job_id = db.create_job(conn, "근로장려금 신청 자격")
    orch.run_once()
    req = svc.telegram.requests[0][1]
    real = db.mark_published
    monkeypatch.setattr(db, "mark_published", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("잠김")))
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    assert not getattr(svc.telegram, "videos", [])                  # 장부 기록 전에는 보내지 않음
    monkeypatch.setattr(db, "mark_published", real)
    svc.wp.get_post = lambda pid: WPPost(pid, "publish", f"https://jalcheck.com/post-{pid}/")
    assert orch.reconcile_publishing(older_than_seconds=0) == [job_id]
    assert len(svc.telegram.videos) == 1


def test_draft_gets_category_chosen_from_site_list(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    assert svc.wp.last_categories == [48] and svc.telegram.last_category == "세금·절세"
    assert ("tax", "세금·절세", "내고 돌려받는 세금") in svc.generator.category_options
    assert not any("분류" in w for w in svc.telegram.requests[-1][2])


def test_category_failure_keeps_default_and_warns(env):
    from src.ai_generator import GenerationError
    orch, svc, conn, _ = env
    svc.generator.category = GenerationError("목록에 없는 분류를 골랐습니다: x")
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    assert svc.wp.last_categories is None and svc.telegram.last_category == ""
    assert db.get_job(conn, job_id)["status"] == "awaiting_approval"
    assert any(w.startswith("분류 자동 선택 실패") for w in svc.telegram.requests[-1][2])


def test_resume_with_old_draft_checkpoint_skips_category_and_keeps_warning(env):
    from src.ai_generator import GenerationError
    orch, svc, conn, _ = env
    svc.telegram.fail_request = 1                    # 글 저장 뒤 승인 요청에서 멈춘 작업
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    pid = db.get_job(conn, job_id)["post_id"]
    assert db.get_job(conn, job_id)["status"] == "failed" and pid
    # 분류 기능 전의 체크포인트(분류 키 없음)로 다시: 분류를 다시 고르지 않고 승인 요청은 그대로
    db.save_checkpoint(conn, job_id, "draft", {"post_id": pid, "status": "draft", "link": "x", "featured": None})
    svc.generator.category = GenerationError("다시 부르면 안 됨")
    svc.wp.last_categories = "unchanged"
    db.set_status(conn, job_id, "queued")
    orch.run_once()
    assert db.get_job(conn, job_id)["status"] == "awaiting_approval" and svc.telegram.last_category == ""
    assert not any("분류" in w for w in svc.telegram.requests[-1][2]) and svc.wp.last_categories == "unchanged"


def test_resume_keeps_saved_category_warning(env):
    orch, svc, conn, _ = env
    svc.telegram.fail_request = 1
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    pid = db.get_job(conn, job_id)["post_id"]
    db.save_checkpoint(conn, job_id, "draft", {"post_id": pid, "status": "draft", "link": "x", "featured": None,
                                               "category": "", "category_warning": "분류 자동 선택 실패 — 시험"})
    svc.wp.last_categories = "unchanged"
    db.set_status(conn, job_id, "queued")
    orch.run_once()
    assert svc.wp.last_categories == "unchanged" and db.get_job(conn, job_id)["status"] == "awaiting_approval"
    assert svc.telegram.requests[-1][2][-1] == "분류 자동 선택 실패 — 시험"


def test_category_failure_on_existing_draft_does_not_send_categories(env):
    from src.ai_generator import GenerationError
    from src.pipeline import make_draft
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "주제")
    orch.run_once()
    pid = db.get_job(conn, job_id)["post_id"]
    svc.generator.category = GenerationError("x")
    content = MasterContent.model_validate(good_data())
    media = {"blog": [None] * len(content.sections), "scenes": []}
    out = make_draft(svc, job_id, content, [], media, pid)
    assert out["post_id"] == pid and svc.wp.last_categories is None      # 원래 분류를 건드리지 않음
    assert "분류를 바꾸지 않고 저장" in out["category_warning"]


def _mismatched_content(narration):
    data = good_data()
    data["shorts"]["scenes"][0]["narration"] = narration
    return MasterContent.model_validate(data)


def test_approval_warns_when_shorts_number_not_in_post(env):
    orch, svc, conn, _ = env
    svc.generator.generate = lambda *a, **k: GenerationResult(_mismatched_content("최대 9,876만 원까지"), "m", 1, [])
    db.create_job(conn, "주제")
    orch.run_once()
    warnings = svc.telegram.requests[-1][2]
    assert any("글 본문에 없는 것" in w and "장면 1: 9,876" in w for w in warnings)


def test_upload_kit_warns_first_when_post_edited_after_approval(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "근로장려금 신청 자격")
    orch.run_once()
    req = svc.telegram.requests[0][1]
    pid = db.get_job(conn, job_id)["post_id"]
    svc.telegram.texts.clear()
    svc.wp.edited = {pid: "<p>직접 고친 본문</p>"}
    cp = db.get_job(conn, job_id)["checkpoint"]["content"]       # 영상 대사에 숫자가 있는 작업
    cp["content"]["shorts"]["scenes"][0]["narration"] = "2026년 기준 1,200원"
    db.save_checkpoint(conn, job_id, "content", cp)
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    warn, title, desc = svc.telegram.texts
    assert "올리기 전 확인 필요" in warn and "장면 1:" in warn
    assert title == good_data()["shorts"]["title"] and len(svc.telegram.videos) == 1


def test_upload_kit_still_sent_when_number_check_fails(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "근로장려금 신청 자격")
    orch.run_once()
    req = svc.telegram.requests[0][1]
    svc.telegram.texts.clear()
    svc.wp.fail_content = True
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    assert len(svc.telegram.texts) == 2 and len(svc.telegram.videos) == 1


def test_upload_kit_still_sent_when_number_warning_fails_to_send(env):
    orch, svc, conn, _ = env
    job_id = db.create_job(conn, "근로장려금 신청 자격")
    orch.run_once()
    req = svc.telegram.requests[0][1]
    pid = db.get_job(conn, job_id)["post_id"]
    cp = db.get_job(conn, job_id)["checkpoint"]["content"]
    cp["content"]["shorts"]["scenes"][0]["narration"] = "2026년 기준 1,200원"
    db.save_checkpoint(conn, job_id, "content", cp)
    svc.wp.edited = {pid: "<p>직접 고친 본문</p>"}
    sent = []

    def flaky_send(text):
        if "확인 필요" in text:
            raise RuntimeError("텔레그램 오류 시험")
        sent.append(text)
    svc.telegram.send_text = flaky_send
    assert orch.handle_decision(decision(job_id, "publish", req.nonce)) == "발행했습니다"
    assert len(sent) == 2 and len(svc.telegram.videos) == 1
