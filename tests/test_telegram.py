import pytest

from src.telegram_notifier import TelegramError, TelegramNotifier, find_chat_id

CHAT = "555"


class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


class FakeTG:
    def __init__(self, updates=None):
        self.calls = []
        self.updates = updates or []
        self.next_msg = 10

    def post(self, url, timeout=None, json=None, data=None, files=None):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json, data, files))
        if method == "getUpdates":
            return FakeResp({"ok": True, "result": self.updates})
        self.next_msg += 1
        return FakeResp({"ok": True, "result": {"message_id": self.next_msg}})


def make(updates=None, token="123:SECRET"):
    fake = FakeTG(updates)
    return TelegramNotifier(token, CHAT, session=fake), fake


def callback(n, data, chat=CHAT, user=CHAT, update_id=1):
    return {"update_id": update_id, "callback_query": {
        "id": f"cb{update_id}", "data": data, "from": {"id": int(user)},
        "message": {"message_id": 77, "chat": {"id": int(chat)}}}}


def test_approval_request_has_signed_buttons_and_escaped_text(tmp_path):
    tg, fake = make()
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")
    shot = tmp_path / "post26_mobile.png"
    shot.write_bytes(b"png")
    mid = tg.send_approval_request(7, "<제목>", "요약 & 설명",
                                   "https://jalcheck.com/wp-admin/post.php?post=26&action=edit",
                                   warnings=["숫자 확인"], video_path=video,
                                   screenshots=[shot, tmp_path / "없는파일.png"])
    assert [c[0] for c in fake.calls] == ["sendDocument", "sendVideo", "sendMessage"] and mid.message_id == 13
    assert "(mobile)" in fake.calls[0][2]["caption"]
    body = fake.calls[2][1]
    assert "action=edit" in body["text"]
    assert body["chat_id"] == CHAT and "&lt;제목&gt;" in body["text"] and "요약 &amp; 설명" in body["text"]
    buttons = body["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["발행", "보류", "취소"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)
    assert tg.parse_callback(buttons[0]["callback_data"]) == (7, "publish", mid.nonce)
    again = tg.send_approval_request(7, "t", "s", "https://x/?a=1")
    assert again.nonce != mid.nonce                              # 요청마다 새 요청 번호


def test_poll_accepts_only_our_chat_user_and_signature():
    tg, _ = make()
    good = tg.callback_data(7, "p", "aabbccdd")
    forged = "p:7:aabbccdd:0000000000000000"
    other_bot = TelegramNotifier("999:OTHER", CHAT, session=FakeTG()).callback_data(7, "p", "aabbccdd")
    updates = [
        callback(1, good, update_id=1),
        callback(2, forged, update_id=2),
        callback(3, good, chat="666", update_id=3),
        callback(4, good, user="666", update_id=4),
        callback(5, other_bot, update_id=5),
        {"update_id": 6, "message": {"text": "hi"}},
        callback(7, tg.callback_data(8, "c", "11223344"), update_id=7),
        callback(8, "p:1:aabbccdd:한한한한한한한한한한한한한한한한", update_id=8),   # 이상한 글자: 멈추지 않고 거름
    ]
    tg2, fake = make(updates)
    decisions, nxt = tg2.poll_decisions(None)
    assert [(d.job_id, d.action, d.nonce) for d in decisions] == [(7, "publish", "aabbccdd"),
                                                                   (8, "cancel", "11223344")]
    assert nxt == 9
    rejected = [c for c in fake.calls if c[0] == "answerCallbackQuery"]
    assert len(rejected) == 5                                    # 거른 버튼에는 안내만


def test_parse_callback_rejects_garbage():
    tg, _ = make()
    for data in ["", "p", "p:x:aabbccdd:abc", "d:7:aabbccdd:" + tg._sign("d:7:aabbccdd"), "p:7",
                 "p:7:aabbccdd:" + tg._sign("p:7:aabbccdd").upper()]:
        assert tg.parse_callback(data) is None


def test_errors_hide_token():
    import requests

    class Boom:
        def post(self, url, **kw):
            raise requests.ConnectionError(f"failed {url}")

    tg = TelegramNotifier("123:SECRET", CHAT, session=Boom())
    with pytest.raises(TelegramError) as exc:
        tg.send_text("x")
    assert "SECRET" not in str(exc.value) and exc.value.__cause__ is None

    class Bad:
        def post(self, url, **kw):
            return FakeResp({"ok": False, "description": "Unauthorized"}, 401)

    with pytest.raises(TelegramError, match="Unauthorized"):
        TelegramNotifier("123:SECRET", CHAT, session=Bad()).send_text("x")


def test_requires_settings_and_private_chat():
    with pytest.raises(TelegramError):
        TelegramNotifier("", CHAT)
    with pytest.raises(TelegramError, match="그룹"):
        TelegramNotifier("1:x", "-1001234567")


def test_long_fields_are_clipped_before_html(tmp_path):
    tg, fake = make()
    tg.send_approval_request(1, "가" * 1000 + "<b>", "나" * 2000, "https://x", warnings=["다" * 999] * 20)
    text = fake.calls[-1][1]["text"]
    assert len(text) < 4096 and text.count("<b>") == text.count("</b>")
    assert "&lt;b&gt;" not in text                               # 잘린 뒤라 끝의 <b>는 없음


def test_big_video_sends_note_instead(tmp_path, monkeypatch):
    from src import telegram_notifier as tn
    monkeypatch.setattr(tn, "MAX_VIDEO_BYTES", 3)
    tg, fake = make()
    v = tmp_path / "big.mp4"
    v.write_bytes(b"12345")
    tg.send_video(v, "cap")
    assert fake.calls[0][0] == "sendMessage" and "50MB" in fake.calls[0][1]["text"]


def test_mark_done_removes_buttons():
    tg, fake = make()
    tg.mark_done(77, "발행 완료")
    assert [c[0] for c in fake.calls] == ["editMessageReplyMarkup", "sendMessage"]


def test_find_chat_id():
    fake = FakeTG([{"update_id": 1, "message": {"chat": {"id": 555, "type": "private", "first_name": "대홍"}}}])
    assert find_chat_id("t", session=fake) == [{"id": 555, "type": "private", "name": "대홍"}]


def test_trailing_newline_and_zero_chat_rejected():
    tg, _ = make()
    good = tg.callback_data(7, "p", "aabbccdd")
    assert tg.parse_callback(good) is not None and tg.parse_callback(good + "\n") is None
    for bad in ("0", "0123"):
        with pytest.raises(TelegramError):
            TelegramNotifier("1:x", bad)
