import json
from types import SimpleNamespace

import pytest

from src.image_judge import GeminiImageJudge, JudgeError


class FakeModels:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def generate_content(self, **kw):
        self.calls.append(kw)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(text=self.reply)


def make(reply):
    models = FakeModels(reply)
    return GeminiImageJudge(SimpleNamespace(models=models), "m"), models


def previews(tmp_path, n=3):
    out = []
    for i in range(n):
        p = tmp_path / f"w{i}.jpg"
        p.write_bytes(b"\xff\xd8x")
        out.append(p)
    return out


def test_returns_index_and_sends_all_images(tmp_path):
    judge, models = make(json.dumps({"best": 1, "reason": "집 사진"}))
    assert judge("종부세", "house tax", previews(tmp_path)) == 1
    contents = models.calls[0]["contents"]
    assert "종부세" in contents[0] and len(contents) == 1 + 3 * 2


def test_minus_one_means_none(tmp_path):
    judge, _ = make(json.dumps({"best": -1, "reason": "모두 로고"}))
    assert judge("t", "q", previews(tmp_path)) is None


@pytest.mark.parametrize("reply", ["not json", json.dumps({"best": 7, "reason": ""}), RuntimeError("429")])
def test_bad_reply_raises(tmp_path, reply):
    judge, _ = make(reply)
    with pytest.raises(JudgeError):
        judge("t", "q", previews(tmp_path))
