import pytest

from src import config
from src.config import MissingSettingError, load_settings

ENV_NAMES = [
    "WP_BASE_URL", "WP_USERNAME", "WP_APP_PASSWORD", "GEMINI_API_KEY", "GEMINI_MODEL",
    "PEXELS_API_KEY", "PIXABAY_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "GOOGLE_SERVICE_ACCOUNT_FILE", "GOOGLE_SHEET_ID", "TTS_VOICE", "WEB_HOST", "WEB_PORT",
    "DATA_DIR", "FFMPEG_BIN", "FFPROBE_BIN", "FONTS_DIR",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_env_empty(tmp_path):
    s = load_settings(write_env(tmp_path, ""))
    assert s.wp_base_url == "https://jalcheck.com"
    assert s.tts_voice == "ko-KR-SunHiNeural"
    assert s.web_host == "127.0.0.1" and s.web_port == 8000
    assert s.db_path == config.ROOT / "data" / "jobs.sqlite3"


def test_blank_values_fall_back_to_defaults(tmp_path):
    s = load_settings(write_env(tmp_path, "DATA_DIR=\nWEB_PORT=\nTTS_VOICE=\n"))
    assert s.data_dir == config.ROOT / "data"
    assert s.web_port == 8000
    assert s.tts_voice == "ko-KR-SunHiNeural"


def test_reads_values_and_resolves_relative_paths(tmp_path):
    s = load_settings(write_env(
        tmp_path,
        "WP_BASE_URL=https://example.com/\nWEB_PORT=9001\nDATA_DIR=output\n"
        f"FONTS_DIR={tmp_path}\n",
    ))
    assert s.wp_base_url == "https://example.com"
    assert s.web_port == 9001
    assert s.data_dir == config.ROOT / "output"
    assert s.fonts_dir == tmp_path


def test_existing_environment_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_VOICE", "ko-KR-InJoonNeural")
    s = load_settings(write_env(tmp_path, "TTS_VOICE=ko-KR-SunHiNeural\n"))
    assert s.tts_voice == "ko-KR-InJoonNeural"


def test_bad_port_is_reported(tmp_path):
    with pytest.raises(ValueError, match="WEB_PORT"):
        load_settings(write_env(tmp_path, "WEB_PORT=abc\n"))


def test_require_lists_missing_env_names(tmp_path):
    s = load_settings(write_env(tmp_path, "WP_USERNAME=admin\n"))
    s.require("wp_username")
    with pytest.raises(MissingSettingError) as exc:
        s.require("wp_username", "wp_app_password", "gemini_api_key")
    assert "WP_APP_PASSWORD" in str(exc.value)
    assert "GEMINI_API_KEY" in str(exc.value)
    assert "WP_USERNAME" not in str(exc.value)


def test_require_rejects_typos(tmp_path):
    s = load_settings(write_env(tmp_path, ""))
    with pytest.raises(ValueError, match="알 수 없는"):
        s.require("wp_pasword")


def test_secrets_hidden_in_repr(tmp_path):
    s = load_settings(write_env(
        tmp_path, "GEMINI_API_KEY=sk-very-secret\nWP_APP_PASSWORD=abcd efgh\n",
    ))
    text = repr(s) + str(s)
    assert "sk-very-secret" not in text
    assert "abcd efgh" not in text
    assert "gemini_api_key='***'" in text
