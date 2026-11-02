"""환경변수(.env)와 경로 설정을 한곳에서 읽는다.

비밀값이 없어도 import는 되게 두고, 실제로 쓰는 기능에서 require()로 검사한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# 화면·로그에 값을 보여 주면 안 되는 항목
SECRET_FIELDS = frozenset({
    "wp_app_password",
    "gemini_api_key",
    "pexels_api_key",
    "pixabay_api_key",
    "telegram_bot_token",
})


class MissingSettingError(RuntimeError):
    """필요한 설정값이 .env에 없을 때."""


def _env(name: str, default: str = "") -> str:
    """값이 없거나 비어 있으면 기본값."""
    return os.environ.get(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 값은 숫자여야 합니다: {raw!r}") from exc


def _env_path(name: str, default: Path) -> Path:
    raw = _env(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Settings:
    # 워드프레스 (응용 프로그램 비밀번호 사용)
    wp_base_url: str = "https://jalcheck.com"
    wp_username: str = ""
    wp_app_password: str = ""

    # 글·대본 생성
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

    # 무료 스톡 이미지·영상
    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    # 텔레그램 알림·승인
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 구글 시트
    google_service_account_file: str = ""
    google_sheet_id: str = ""

    # AI 성우
    tts_voice: str = "ko-KR-SunHiNeural"

    # 웹 대시보드 (기본은 서버 안에서만 접속)
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    # 경로
    data_dir: Path = field(default=ROOT / "data")
    ffmpeg_bin: Path = field(default=ROOT / "tools" / "ffmpeg" / "ffmpeg")
    ffprobe_bin: Path = field(default=ROOT / "tools" / "ffmpeg" / "ffprobe")
    fonts_dir: Path = field(default=ROOT / "assets" / "fonts")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    def require(self, *names: str) -> None:
        """기능을 쓰기 전에 필요한 값이 모두 있는지 확인한다."""
        known = {f.name for f in fields(self)}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"알 수 없는 설정 이름: {', '.join(unknown)}")
        missing = [n for n in names if not str(getattr(self, n)).strip()]
        if missing:
            env_names = ", ".join(n.upper() for n in missing)
            raise MissingSettingError(f".env에 다음 값을 넣어 주세요: {env_names}")

    def __repr__(self) -> str:
        parts = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in SECRET_FIELDS:
                value = "***" if value else ""
            parts.append(f"{f.name}={value!r}")
        return f"Settings({', '.join(parts)})"

    __str__ = __repr__


def load_settings(env_file: Path | None = None) -> Settings:
    """.env를 읽어 Settings를 만든다. 이미 있는 환경변수가 .env보다 우선한다."""
    load_dotenv(env_file or ROOT / ".env", override=False)
    d = Settings()
    return Settings(
        wp_base_url=_env("WP_BASE_URL", d.wp_base_url).rstrip("/"),
        wp_username=_env("WP_USERNAME"),
        wp_app_password=_env("WP_APP_PASSWORD"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL", d.gemini_model),
        pexels_api_key=_env("PEXELS_API_KEY"),
        pixabay_api_key=_env("PIXABAY_API_KEY"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        google_service_account_file=_env("GOOGLE_SERVICE_ACCOUNT_FILE"),
        google_sheet_id=_env("GOOGLE_SHEET_ID"),
        tts_voice=_env("TTS_VOICE", d.tts_voice),
        web_host=_env("WEB_HOST", d.web_host),
        web_port=_env_int("WEB_PORT", d.web_port),
        data_dir=_env_path("DATA_DIR", d.data_dir),
        ffmpeg_bin=_env_path("FFMPEG_BIN", d.ffmpeg_bin),
        ffprobe_bin=_env_path("FFPROBE_BIN", d.ffprobe_bin),
        fonts_dir=_env_path("FONTS_DIR", d.fonts_dir),
    )
