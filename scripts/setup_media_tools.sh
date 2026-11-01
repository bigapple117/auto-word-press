#!/usr/bin/env bash
# FFmpeg(ARM64/AMD64 정적 빌드)와 한글 폰트를 프로젝트 폴더 안에 받습니다. sudo 불필요.
# 결과: tools/ffmpeg/{ffmpeg,ffprobe}, assets/fonts/NotoSansCJKkr-{Regular,Bold}.otf
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/tools/ffmpeg"
FONTS="$ROOT/assets/fonts"
mkdir -p "$TOOLS" "$FONTS"

case "$(uname -m)" in
  aarch64|arm64) ARCH=arm64 ;;
  x86_64) ARCH=amd64 ;;
  *) echo "지원하지 않는 CPU 구조: $(uname -m)" >&2; exit 1 ;;
esac

if [ ! -x "$TOOLS/ffmpeg" ]; then
  BASE="https://johnvansickle.com/ffmpeg/releases"
  NAME="ffmpeg-release-$ARCH-static.tar.xz"
  TMP="$(mktemp -d)"
  trap 'rm -r "$TMP"' EXIT
  curl -fsSL -o "$TMP/$NAME" "$BASE/$NAME"
  curl -fsSL -o "$TMP/$NAME.md5" "$BASE/$NAME.md5"
  (cd "$TMP" && md5sum -c "$NAME.md5")
  tar -xJf "$TMP/$NAME" -C "$TMP"
  cp "$TMP"/ffmpeg-*-static/ffmpeg "$TMP"/ffmpeg-*-static/ffprobe "$TOOLS/"
fi

FONT_BASE="https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/Korean"
for f in NotoSansCJKkr-Regular.otf NotoSansCJKkr-Bold.otf; do
  [ -s "$FONTS/$f" ] || curl -fsSL -o "$FONTS/$f" "$FONT_BASE/$f"
done

"$TOOLS/ffmpeg" -hide_banner -version | head -1
ls -lh "$FONTS"
